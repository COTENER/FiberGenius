from django.core.management.base import BaseCommand

from mapas.models import InventarioODF
from mapas.services.inventario import recalcular_contadores_odf


class Command(BaseCommand):
    help = "Recalcula y reporta los contadores derivados de todos los ODF."

    def handle(self, *args, **options):
        total = discrepantes = 0
        for odf in InventarioODF.objects.select_related(
            "rack_obj__sala__hub_site"
        ).iterator():
            total += 1
            resultado = recalcular_contadores_odf(odf)
            if resultado["discrepancias"]:
                discrepantes += 1
                self.stdout.write(
                    f"{odf.odf}: reconciliado "
                    f"({resultado['puertos_ocupados']} ocupados, "
                    f"{resultado['puertos_reservados']} reservados, "
                    f"{resultado['puertos_libres']} libres)"
                )
        self.stdout.write(self.style.SUCCESS(
            f"ODF procesados: {total}; con discrepancias corregidas: {discrepantes}."
        ))
