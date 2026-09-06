"""Operaciones transaccionales para la ocupación de puertos ODF."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q

from ..models import (
    AuditoriaPuertoODF,
    DetallePuertoODF,
    InventarioFibra,
    InventarioODF,
    TerminacionFibra,
)
from .fibras import crear_fibra, establecer_estado_fibra_informado
from .ubicacion import nombre_site


class MovimientoRequiereConfirmacion(ValidationError):
    def __init__(self, terminacion):
        puerto = terminacion.puerto_odf
        self.terminacion = terminacion
        super().__init__(
            f"El extremo {terminacion.extremo} de "
            f"{terminacion.fibra.nombre_troncal} / "
            f"{terminacion.fibra.fibra_numero} "
            f"ya está conectado en {puerto.odf_obj.odf} / Puerto {puerto.puerto_odf}."
        )


def _bloquear_fibra(fibra_id):
    try:
        return (
            InventarioFibra.objects.select_for_update()
            .select_related("ruta")
            .get(pk=fibra_id)
        )
    except InventarioFibra.DoesNotExist as exc:
        raise ValidationError("La fibra seleccionada no existe.") from exc


def _bloquear_terminacion(fibra, extremo):
    return (
        TerminacionFibra.objects.select_for_update()
        .select_related("fibra__ruta", "puerto_odf__odf_obj")
        .filter(fibra=fibra, extremo=extremo)
        .order_by("pk")
        .first()
    )


def _bloquear_puertos(puerto_ids):
    ids = sorted({int(puerto_id) for puerto_id in puerto_ids})
    puertos = {
        puerto.pk: puerto
        for puerto in (
            DetallePuertoODF.objects.select_for_update()
            .select_related("odf_obj")
            .filter(pk__in=ids)
            .order_by("pk")
        )
    }
    faltantes = [puerto_id for puerto_id in ids if puerto_id not in puertos]
    if faltantes:
        raise ValidationError("El puerto ODF no existe.")
    return puertos


def _bloquear_puerto(puerto_id):
    return _bloquear_puertos((puerto_id,))[int(puerto_id)]


def _actualizar_contadores(*odfs):
    procesados = set()
    for odf in odfs:
        if odf and odf.pk not in procesados:
            odf.actualizar_contadores()
            procesados.add(odf.pk)


def _establecer_estado(puerto, estado):
    DetallePuertoODF.objects.filter(pk=puerto.pk).update(estado_puerto=estado)
    puerto.estado_puerto = estado


def _referencia_puerto(puerto):
    if not puerto:
        return ""
    odf = puerto.odf_obj
    return f"{nombre_site(odf)} / {odf.odf} / Puerto {puerto.puerto_odf}"


def _registrar_auditoria(
    *, accion, usuario=None, origen="SISTEMA", lote_importacion=None,
    fibra=None, extremo="", puerto_anterior=None, puerto_nuevo=None,
    estado_anterior="", estado_nuevo="", sincronizo_fibra=False,
    metadatos=None,
):
    origen = str(origen or "SISTEMA").strip().upper()
    if origen not in {clave for clave, _ in AuditoriaPuertoODF.ORIGENES}:
        origen = "SISTEMA"
    if usuario is not None and not getattr(usuario, "is_authenticated", False):
        usuario = None
    AuditoriaPuertoODF.objects.create(
        accion=accion,
        origen=origen,
        usuario=usuario,
        fibra=fibra,
        extremo=extremo,
        puerto_anterior=puerto_anterior,
        puerto_nuevo=puerto_nuevo,
        estado_anterior=estado_anterior or "",
        estado_nuevo=estado_nuevo or "",
        referencia_puerto_anterior=_referencia_puerto(puerto_anterior),
        referencia_puerto_nuevo=_referencia_puerto(puerto_nuevo),
        referencia_fibra=(
            f"{fibra.nombre_troncal} / {fibra.fibra_numero}" if fibra else ""
        ),
        sincronizo_fibra=bool(sincronizo_fibra),
        lote_importacion=lote_importacion,
        metadatos=metadatos or {},
    )


def _lotes(valores, tamano=500):
    valores = list(valores)
    for indice in range(0, len(valores), tamano):
        yield valores[indice:indice + tamano]


def _actualizar_contadores_masivo(odf_ids):
    """Recalcula contadores de varios ODF sin ejecutar una consulta por ODF."""
    odf_ids = sorted({int(odf_id) for odf_id in odf_ids})
    actualizaciones = []
    for lote_ids in _lotes(odf_ids):
        resumenes = {
            fila["odf_obj_id"]: fila
            for fila in (
                DetallePuertoODF.objects
                .filter(odf_obj_id__in=lote_ids)
                .values("odf_obj_id")
                .annotate(
                    ocupados=Count("id", filter=Q(estado_puerto="OCUPADO")),
                    reservados=Count("id", filter=Q(estado_puerto="RESERVADO")),
                    libres=Count("id", filter=Q(estado_puerto="LIBRE")),
                )
            )
        }
        for odf_id in lote_ids:
            resumen = resumenes.get(odf_id, {})
            actualizaciones.append(InventarioODF(
                pk=odf_id,
                puertos_ocupados=resumen.get("ocupados", 0),
                puertos_reservados=resumen.get("reservados", 0),
                puertos_libres=resumen.get("libres", 0),
            ))
    if actualizaciones:
        InventarioODF.objects.bulk_update(
            actualizaciones,
            ["puertos_ocupados", "puertos_reservados", "puertos_libres"],
            batch_size=500,
        )


@transaction.atomic
def conectar_puertos_masivo(
    conexiones,
    *,
    usuario=None,
    origen="SISTEMA",
    lote_importacion=None,
):
    """Conecta un lote ya validado con las mismas invariantes del servicio unitario.

    Cada elemento debe aportar ``fibra``, ``puerto``, ``extremo`` y puede aportar
    ``tipo_conector``. El bloqueo y las restricciones se vuelven a comprobar dentro
    de la transacción; ``bulk_create`` evita una operación SQL por terminación.
    """
    conexiones = list(conexiones)
    if not conexiones:
        return [], 0, 0

    normalizadas = []
    claves_extremo = set()
    ids_puerto = set()
    ids_fibra = set()
    for conexion in conexiones:
        fibra_id = int(getattr(conexion.get("fibra"), "pk", 0) or 0)
        puerto_id = int(getattr(conexion.get("puerto"), "pk", 0) or 0)
        extremo = str(conexion.get("extremo") or "").strip().upper()
        if not fibra_id:
            raise ValidationError("La fibra seleccionada no existe.")
        if not puerto_id:
            raise ValidationError("El puerto ODF no existe.")
        if extremo not in {"A", "B"}:
            raise ValidationError("Seleccione el extremo A o B.")
        clave_extremo = (fibra_id, extremo)
        if clave_extremo in claves_extremo:
            raise ValidationError(
                "El mismo extremo de una fibra está repetido en la operación masiva."
            )
        if puerto_id in ids_puerto:
            raise ValidationError(
                "El mismo puerto ODF está repetido en la operación masiva."
            )
        tipo_conector = str(conexion.get("tipo_conector") or "").strip()
        if len(tipo_conector) > 100:
            raise ValidationError("El tipo de conector no puede superar 100 caracteres.")
        claves_extremo.add(clave_extremo)
        ids_puerto.add(puerto_id)
        ids_fibra.add(fibra_id)
        normalizadas.append({
            "fibra_id": fibra_id,
            "puerto_id": puerto_id,
            "extremo": extremo,
            "tipo_conector": tipo_conector,
        })

    fibras = {}
    for lote_ids in _lotes(sorted(ids_fibra)):
        for fibra in (
            InventarioFibra.objects.select_for_update()
            .select_related("ruta")
            .filter(pk__in=lote_ids)
            .order_by("pk")
        ):
            fibras[fibra.pk] = fibra
    if set(fibras) != ids_fibra:
        raise ValidationError("Una de las fibras seleccionadas ya no existe.")

    por_extremo = {}
    for lote_ids in _lotes(sorted(ids_fibra)):
        for terminacion in (
            TerminacionFibra.objects.select_for_update()
            .select_related("fibra__ruta", "puerto_odf__odf_obj")
            .filter(fibra_id__in=lote_ids)
            .order_by("pk")
        ):
            por_extremo[(terminacion.fibra_id, terminacion.extremo)] = terminacion

    puertos = {}
    for lote_ids in _lotes(sorted(ids_puerto)):
        for puerto in (
            DetallePuertoODF.objects.select_for_update()
            .select_related("odf_obj__rack_obj__sala__hub_site")
            .filter(pk__in=lote_ids)
            .order_by("pk")
        ):
            puertos[puerto.pk] = puerto
    if set(puertos) != ids_puerto:
        raise ValidationError("Uno de los puertos ODF ya no existe.")

    por_puerto = {}
    for lote_ids in _lotes(sorted(ids_puerto)):
        for terminacion in (
            TerminacionFibra.objects
            .select_related("fibra__ruta", "puerto_odf__odf_obj")
            .filter(puerto_odf_id__in=lote_ids)
            .order_by("pk")
        ):
            por_puerto[terminacion.puerto_odf_id] = terminacion

    origen = str(origen or "SISTEMA").strip().upper()
    if origen not in {clave for clave, _ in AuditoriaPuertoODF.ORIGENES}:
        origen = "SISTEMA"
    if usuario is not None and not getattr(usuario, "is_authenticated", False):
        usuario = None

    nuevas = []
    existentes_actualizadas = []
    puertos_actualizados = []
    auditorias = []
    resultados = []
    odf_ids_actualizados = set()
    creadas = actualizadas = 0

    for conexion in normalizadas:
        fibra = fibras[conexion["fibra_id"]]
        puerto = puertos[conexion["puerto_id"]]
        extremo = conexion["extremo"]
        existente = por_extremo.get((fibra.pk, extremo))
        ocupante = por_puerto.get(puerto.pk)
        if ocupante and (not existente or ocupante.pk != existente.pk):
            raise ValidationError(
                "El puerto ya está conectado a otra fibra. Desconéctelo antes de reutilizarlo."
            )
        if existente and existente.puerto_odf_id != puerto.pk:
            raise MovimientoRequiereConfirmacion(existente)
        if puerto.estado_puerto not in {"LIBRE", "RESERVADO", "OCUPADO"}:
            raise ValidationError("El puerto no tiene un estado válido para conectarlo.")

        conector = (
            conexion["tipo_conector"]
            or (existente.tipo_conector if existente else "")
            or puerto.tipo_conector
            or puerto.odf_obj.tipo_conector
            or fibra.tipo_conector
            or ""
        )
        if len(conector) > 100:
            raise ValidationError("El tipo de conector no puede superar 100 caracteres.")

        estado_anterior = puerto.estado_puerto
        cambio_estado = estado_anterior != "OCUPADO"
        if cambio_estado:
            puerto.estado_puerto = "OCUPADO"
            puertos_actualizados.append(puerto)
            odf_ids_actualizados.add(puerto.odf_obj_id)

        if existente:
            cambios_terminacion = False
            if existente.tipo_conector != conector:
                existente.tipo_conector = conector
                cambios_terminacion = True
            if existente.lote_importacion_id != getattr(lote_importacion, "pk", None):
                existente.lote_importacion = lote_importacion
                cambios_terminacion = True
            if cambios_terminacion:
                existentes_actualizadas.append(existente)
            terminacion = existente
            actualizadas += 1
        else:
            terminacion = TerminacionFibra(
                fibra=fibra,
                extremo=extremo,
                puerto_odf=puerto,
                tipo_conector=conector,
                lote_importacion=lote_importacion,
            )
            nuevas.append(terminacion)
            creadas += 1

        if existente is None or cambio_estado:
            auditorias.append(AuditoriaPuertoODF(
                accion="CONECTAR",
                origen=origen,
                usuario=usuario,
                fibra=fibra,
                extremo=extremo,
                puerto_anterior=puerto if existente else None,
                puerto_nuevo=puerto,
                estado_anterior=estado_anterior,
                estado_nuevo="OCUPADO",
                referencia_puerto_anterior=(
                    _referencia_puerto(puerto) if existente else ""
                ),
                referencia_puerto_nuevo=_referencia_puerto(puerto),
                referencia_fibra=f"{fibra.nombre_troncal} / {fibra.fibra_numero}",
                sincronizo_fibra=False,
                lote_importacion=lote_importacion,
                metadatos={"conexion_existente": True} if existente else {},
            ))
        resultados.append(terminacion)

    if nuevas:
        TerminacionFibra.objects.bulk_create(nuevas, batch_size=500)
    if existentes_actualizadas:
        TerminacionFibra.objects.bulk_update(
            existentes_actualizadas,
            ["tipo_conector", "lote_importacion"],
            batch_size=500,
        )
    if puertos_actualizados:
        DetallePuertoODF.objects.bulk_update(
            puertos_actualizados,
            ["estado_puerto"],
            batch_size=500,
        )
    if auditorias:
        AuditoriaPuertoODF.objects.bulk_create(auditorias, batch_size=500)
    _actualizar_contadores_masivo(odf_ids_actualizados)
    return resultados, creadas, actualizadas


@transaction.atomic
def conectar_puerto(
    *,
    puerto_id,
    fibra_id=None,
    fibra_numero="",
    extremo,
    permitir_mover=False,
    ocupar_fibra=False,
    usuario=None,
    origen="SISTEMA",
    lote_importacion=None,
):
    extremo = str(extremo or "").strip().upper()
    if extremo not in {"A", "B"}:
        raise ValidationError("Seleccione el extremo A o B.")

    if not fibra_id:
        numero_provisional = str(fibra_numero or "").strip().upper()
        if not numero_provisional:
            raise ValidationError(
                "Indique el número del hilo que quedará con troncal pendiente."
            )
        if len(numero_provisional) > 50:
            raise ValidationError("El número del hilo no puede superar 50 caracteres.")
        fibra = crear_fibra(
            fibra_numero=numero_provisional,
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
        fibra_id = fibra.pk

    # Orden oficial de toda operación de conectividad:
    # InventarioFibra -> TerminacionFibra -> puertos por PK ascendente.
    fibra = _bloquear_fibra(fibra_id)
    existente = _bloquear_terminacion(fibra, extremo)
    ids_puerto = {int(puerto_id)}
    if existente:
        ids_puerto.add(existente.puerto_odf_id)
    puertos_bloqueados = _bloquear_puertos(ids_puerto)
    puerto = puertos_bloqueados[int(puerto_id)]

    # El puerto ya está bloqueado y funciona como mutex físico. No se toma
    # aquí otra terminación fuera de orden; la unicidad de puerto protege la
    # escritura y la lectura confirma el ocupante comprometido más reciente.
    ocupante = (
        TerminacionFibra.objects
        .select_related("fibra__ruta", "puerto_odf__odf_obj")
        .filter(puerto_odf=puerto)
        .first()
    )
    if ocupante and (not existente or ocupante.pk != existente.pk):
        raise ValidationError(
            "El puerto ya está conectado a otra fibra. Desconéctelo antes de reutilizarlo."
        )
    estado_destino_anterior = puerto.estado_puerto
    if existente and existente.puerto_odf_id == puerto.pk:
        cambio_puerto = puerto.estado_puerto != "OCUPADO"
        if puerto.estado_puerto != "OCUPADO":
            _establecer_estado(puerto, "OCUPADO")
            _actualizar_contadores(puerto.odf_obj)
        if ocupar_fibra:
            establecer_estado_fibra_informado(
                fibra=fibra,
                estado="OCUPADO",
                usuario=usuario,
                origen=origen,
                lote_importacion=lote_importacion,
            )
        if cambio_puerto or ocupar_fibra:
            _registrar_auditoria(
                accion="CONECTAR",
                usuario=usuario,
                origen=origen,
                lote_importacion=lote_importacion,
                fibra=fibra,
                extremo=extremo,
                puerto_anterior=puerto,
                puerto_nuevo=puerto,
                estado_anterior=estado_destino_anterior,
                estado_nuevo="OCUPADO",
                sincronizo_fibra=ocupar_fibra,
                metadatos={"conexion_existente": True},
            )
        return existente, False
    if existente and not permitir_mover:
        raise MovimientoRequiereConfirmacion(existente)
    if puerto.estado_puerto not in {"LIBRE", "RESERVADO", "OCUPADO"}:
        raise ValidationError("El puerto no tiene un estado válido para conectarlo.")

    odf_anterior = None
    puerto_anterior = None
    estado_puerto_origen = ""
    if existente:
        puerto_anterior = puertos_bloqueados[existente.puerto_odf_id]
        estado_puerto_origen = puerto_anterior.estado_puerto
        odf_anterior = puerto_anterior.odf_obj
        existente.puerto_odf = puerto
        existente.save(update_fields=["puerto_odf"])
        if puerto_anterior.pk != puerto.pk:
            _establecer_estado(puerto_anterior, "LIBRE")
        terminacion = existente
        creada = False
    else:
        terminacion = TerminacionFibra.objects.create(
            fibra=fibra,
            extremo=extremo,
            puerto_odf=puerto,
            tipo_conector=puerto.tipo_conector or fibra.tipo_conector or "",
        )
        creada = True

    _establecer_estado(puerto, "OCUPADO")
    if ocupar_fibra:
        establecer_estado_fibra_informado(
            fibra=fibra,
            estado="OCUPADO",
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    _actualizar_contadores(odf_anterior, puerto.odf_obj)
    _registrar_auditoria(
        accion="MOVER" if existente else "CONECTAR",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        fibra=fibra,
        extremo=extremo,
        puerto_anterior=puerto_anterior,
        puerto_nuevo=puerto,
        estado_anterior=estado_destino_anterior,
        estado_nuevo="OCUPADO",
        sincronizo_fibra=ocupar_fibra,
        metadatos=(
            {
                "estado_puerto_origen_anterior": estado_puerto_origen,
                "estado_puerto_origen_nuevo": "LIBRE",
            }
            if existente else {}
        ),
    )
    return terminacion, creada


@transaction.atomic
def desconectar_puerto(
    *, puerto_id, liberar_fibra=False, usuario=None, origen="SISTEMA",
    lote_importacion=None,
):
    referencia = (
        TerminacionFibra.objects.filter(puerto_odf_id=puerto_id)
        .values("fibra_id", "extremo")
        .first()
    )
    if referencia is None:
        raise ValidationError(
            "El puerto no tiene una terminación oficial. Debe regularizarse antes de desconectarlo."
        )
    fibra = _bloquear_fibra(referencia["fibra_id"])
    terminacion = _bloquear_terminacion(fibra, referencia["extremo"])
    if terminacion is None or terminacion.puerto_odf_id != int(puerto_id):
        raise ValidationError(
            "La terminación cambió durante la operación. Actualice la vista e intente nuevamente."
        )
    puerto = _bloquear_puertos((puerto_id,))[int(puerto_id)]
    fibra = terminacion.fibra
    extremo = terminacion.extremo
    estado_anterior = puerto.estado_puerto
    terminacion.delete()
    _establecer_estado(puerto, "LIBRE")
    if liberar_fibra:
        establecer_estado_fibra_informado(
            fibra=fibra,
            estado="DISPONIBLE",
            usuario=usuario,
            origen=origen,
            lote_importacion=lote_importacion,
        )
    _actualizar_contadores(puerto.odf_obj)
    _registrar_auditoria(
        accion="DESCONECTAR",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        fibra=fibra,
        extremo=extremo,
        puerto_anterior=puerto,
        estado_anterior=estado_anterior,
        estado_nuevo="LIBRE",
        sincronizo_fibra=liberar_fibra,
    )
    return fibra


@transaction.atomic
def reservar_puerto(
    *, puerto_id, usuario=None, origen="SISTEMA", lote_importacion=None
):
    puerto = _bloquear_puerto(puerto_id)
    if TerminacionFibra.objects.filter(puerto_odf=puerto).exists():
        raise ValidationError("No se puede reservar un puerto que tiene una fibra conectada.")
    if puerto.estado_puerto == "OCUPADO":
        raise ValidationError("Un puerto ocupado debe regularizarse antes de reservarlo.")
    estado_anterior = puerto.estado_puerto
    _establecer_estado(puerto, "RESERVADO")
    _actualizar_contadores(puerto.odf_obj)
    _registrar_auditoria(
        accion="RESERVAR",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        puerto_anterior=puerto,
        puerto_nuevo=puerto,
        estado_anterior=estado_anterior,
        estado_nuevo="RESERVADO",
    )
    return puerto


@transaction.atomic
def cancelar_reserva_puerto(
    *, puerto_id, usuario=None, origen="SISTEMA", lote_importacion=None
):
    puerto = _bloquear_puerto(puerto_id)
    if TerminacionFibra.objects.filter(puerto_odf=puerto).exists():
        raise ValidationError("El puerto tiene una fibra conectada y no puede liberarse como reserva.")
    if puerto.estado_puerto != "RESERVADO":
        raise ValidationError("El puerto no está reservado.")
    estado_anterior = puerto.estado_puerto
    _establecer_estado(puerto, "LIBRE")
    _actualizar_contadores(puerto.odf_obj)
    _registrar_auditoria(
        accion="CANCELAR_RESERVA",
        usuario=usuario,
        origen=origen,
        lote_importacion=lote_importacion,
        puerto_anterior=puerto,
        puerto_nuevo=puerto,
        estado_anterior=estado_anterior,
        estado_nuevo="LIBRE",
    )
    return puerto
