from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import (
    AuditoriaPuertoODF,
    DetallePuertoODF,
    HubSite,
    InventarioODF,
    RackFisico,
    SalaTecnica,
)
from .ubicacion import nombre_site


UBICACION_ODF_SIN_ASIGNAR = "SIN ASIGNAR"


def rack_es_ubicacion_sin_asignar(rack: RackFisico) -> bool:
    """Reconoce exclusivamente la jerarquía controlada para ODF pendientes."""
    esperado = UBICACION_ODF_SIN_ASIGNAR.casefold()
    return all(
        str(valor or "").strip().casefold() == esperado
        for valor in (
            rack.nombre,
            rack.sala.nombre,
            rack.sala.hub_site.nombre,
        )
    )


def recalcular_contadores_odf(odf: InventarioODF) -> dict[str, int]:
    """Reconcilia la caché del ODF desde sus puertos, la fuente oficial."""
    anteriores = {
        "puertos_ocupados": odf.puertos_ocupados or 0,
        "puertos_libres": odf.puertos_libres or 0,
        "puertos_reservados": odf.puertos_reservados or 0,
    }
    odf.actualizar_contadores()
    odf.refresh_from_db(fields=list(anteriores))
    actuales = {campo: getattr(odf, campo) or 0 for campo in anteriores}
    return {
        "discrepancias": sum(
            anteriores[campo] != actuales[campo] for campo in anteriores
        ),
        "capacidad_puertos": odf.capacidad_puertos or 0,
        "puertos_materializados": odf.puertos_detalle.count(),
        **actuales,
    }


def limpiar_texto(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none"} else text


@transaction.atomic
def resolver_rack(hub_nombre: str, sala_nombre: str, rack_nombre: str, lote=None) -> RackFisico:
    hub_nombre = limpiar_texto(hub_nombre)
    sala_nombre = limpiar_texto(sala_nombre)
    rack_nombre = limpiar_texto(rack_nombre)
    if not any((hub_nombre, sala_nombre, rack_nombre)):
        hub_nombre = sala_nombre = rack_nombre = UBICACION_ODF_SIN_ASIGNAR
    faltantes = [
        etiqueta
        for etiqueta, valor in (("hub_site", hub_nombre), ("sala", sala_nombre), ("rack", rack_nombre))
        if not valor
    ]
    if faltantes:
        raise ValidationError(f"Faltan datos de ubicación del ODF: {', '.join(faltantes)}")

    hub = HubSite.objects.filter(nombre__iexact=hub_nombre).first()
    if not hub:
        hub = HubSite.objects.create(nombre=hub_nombre, lote_importacion=lote)
    sala, _ = SalaTecnica.objects.get_or_create(
        hub_site=hub,
        nombre=sala_nombre,
        defaults={"lote_importacion": lote},
    )
    rack, _ = RackFisico.objects.get_or_create(
        sala=sala,
        nombre=rack_nombre,
        defaults={"lote_importacion": lote},
    )
    return rack


@transaction.atomic
def ajustar_puertos_a_capacidad(
    odf: InventarioODF,
    capacidad: int,
    *, usuario=None, origen="SISTEMA", lote=None,
) -> InventarioODF:
    """Materializa los puertos físicos declarados por la capacidad del ODF."""
    try:
        if isinstance(capacidad, bool) or str(capacidad).strip() != str(int(capacidad)):
            raise ValueError
        capacidad = int(capacidad)
    except (TypeError, ValueError) as exc:
        raise ValidationError("La capacidad del ODF debe ser un número entero.") from exc
    if capacidad <= 0:
        raise ValidationError("La capacidad del ODF debe ser mayor que cero.")

    odf = InventarioODF.objects.select_for_update().get(pk=odf.pk)
    anterior = odf.capacidad_puertos
    if anterior != capacidad and usuario is not None and not usuario.is_superuser:
        raise ValidationError("Solo un administrador puede cambiar la capacidad de un ODF existente.")
    puertos = list(
        DetallePuertoODF.objects.select_for_update()
        .filter(odf_obj=odf)
        .prefetch_related("terminaciones_fibra")
    )

    por_numero = {}
    fuera_de_rango = []
    for puerto in puertos:
        texto = (puerto.puerto_odf or "").strip()
        numero = int(texto) if texto.isdigit() else None
        if numero is None or numero <= 0:
            raise ValidationError(
                f"El puerto {texto} usa una numeración especial. No se puede ajustar "
                "automáticamente la capacidad; sus puertos se conservaron."
            )
        if numero in por_numero:
            raise ValidationError(
                f"Los puertos {por_numero[numero].puerto_odf} y {texto} representan "
                "la misma posición numérica. Revise la numeración antes de ajustar."
            )
        if numero is not None and numero > 0:
            por_numero[numero] = puerto
            if numero > capacidad:
                fuera_de_rango.append(puerto)

    bloqueados = [
        puerto
        for puerto in fuera_de_rango
        if (
            puerto.estado_puerto != "LIBRE"
            or bool((puerto.destino or "").strip())
            or bool((puerto.patchcord or "").strip())
            or bool((puerto.observaciones or "").strip())
            or bool((puerto.bandeja or "").strip())
            or bool((puerto.tipo_conector or "").strip() not in {"", odf.tipo_conector or ""})
            or bool(puerto.terminaciones_fibra.all())
        )
    ]
    if bloqueados:
        numeros = ", ".join(puerto.puerto_odf for puerto in bloqueados[:10])
        raise ValidationError(
            "No se puede reducir la capacidad porque los siguientes puertos "
            f"tienen uso, vínculo o información registrada: {numeros}."
        )

    if fuera_de_rango:
        DetallePuertoODF.objects.filter(
            pk__in=[puerto.pk for puerto in fuera_de_rango]
        ).delete()

    restantes_no_numericos = sum(
        1
        for puerto in puertos
        if not (puerto.puerto_odf or "").strip().isdigit()
    )
    existentes_en_rango = sum(1 for numero in por_numero if numero <= capacidad)
    if restantes_no_numericos + existentes_en_rango > capacidad:
        raise ValidationError(
            "La capacidad indicada es menor que la cantidad de puertos "
            "existentes que no pueden ajustarse automáticamente."
        )

    faltantes = [
        DetallePuertoODF(
            odf_obj=odf,
            puerto_odf=str(numero),
            estado_puerto="LIBRE",
            tipo_conector=odf.tipo_conector or "",
            destino="",
            patchcord="",
        )
        for numero in range(1, capacidad + 1)
        if numero not in por_numero
    ]
    if faltantes:
        DetallePuertoODF.objects.bulk_create(faltantes)

    InventarioODF.objects.filter(pk=odf.pk).update(capacidad_puertos=capacidad)
    odf.capacidad_puertos = capacidad
    odf.actualizar_contadores()
    if anterior != capacidad:
        AuditoriaPuertoODF.objects.create(
            accion="AJUSTAR_CAPACIDAD", usuario=usuario, origen=origen,
            lote_importacion=lote,
            referencia_puerto_anterior=odf.odf,
            referencia_puerto_nuevo=odf.odf,
            metadatos={"odf_id": odf.pk, "capacidad_anterior": anterior,
                       "capacidad_nueva": capacidad,
                       "puertos_retirados": [p.puerto_odf for p in fuera_de_rango],
                       "puertos_creados": [p.puerto_odf for p in faltantes]},
        )
    odf.refresh_from_db()
    return odf


@transaction.atomic
def actualizar_odf(odf, cambios, *, usuario=None, origen="SISTEMA", lote=None):
    """Guarda metadatos y capacidad como una sola operación reversible."""
    odf = InventarioODF.objects.select_for_update().get(pk=odf.pk)
    cambios = dict(cambios)
    capacidad = cambios.pop("capacidad_puertos", None)
    permitidos = {"odf", "rack_obj", "tipo_conector", "estado", "observaciones", "lote_importacion"}
    if set(cambios) - permitidos:
        raise ValidationError("Campos de ODF no admitidos.")
    if capacidad is not None and int(capacidad) != odf.capacidad_puertos:
        odf = ajustar_puertos_a_capacidad(
            odf, capacidad, usuario=usuario, origen=origen, lote=lote,
        )
    for campo, valor in cambios.items():
        setattr(odf, campo, valor)
    if cambios:
        odf.save(update_fields=list(cambios))
    return odf


@transaction.atomic
def guardar_odf_normalizado(
    *,
    odf_nombre: str,
    hub_nombre: str,
    sala_nombre: str,
    rack_nombre: str,
    defaults: dict | None = None,
    lote=None,
) -> tuple[InventarioODF, bool]:
    odf_nombre = limpiar_texto(odf_nombre)
    if not odf_nombre:
        raise ValidationError("El nombre del ODF es obligatorio.")
    rack = resolver_rack(hub_nombre, sala_nombre, rack_nombre, lote=lote)
    values = dict(defaults or {})
    if lote is not None:
        values["lote_importacion"] = lote
    existente = InventarioODF.objects.select_for_update().filter(odf__iexact=odf_nombre).first()
    if existente:
        if existente.rack_obj_id != rack.pk:
            if rack_es_ubicacion_sin_asignar(existente.rack_obj):
                values['rack_obj'] = rack
            else:
                raise ValidationError(
                    f'El ODF {odf_nombre} ya existe en '
                    f'{nombre_site(existente)} / '
                    f'{existente.rack_obj.sala.nombre} / {existente.rack_obj.nombre}; '
                    'no puede reasignarse '
                    'silenciosamente a otra ubicación.'
                )
        existente = actualizar_odf(
            existente, values, usuario=getattr(lote, 'usuario', None),
            origen='EXCEL' if lote else 'SISTEMA', lote=lote,
        )
        return existente, False

    return InventarioODF.objects.create(
        rack_obj=rack,
        odf=odf_nombre,
        **values,
    ), True
