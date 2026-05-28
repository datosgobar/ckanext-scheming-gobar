import datetime
import logging
from ckan.plugins import toolkit

log = logging.getLogger(__name__)

# ===========================================================================
# CONSTANTES
# ===========================================================================

# Tipos de distribución que se consideran "datos" (prioridad 1)
DATASET_DISTRIBUTION_TYPES = {
    'http://purl.org/dc/dcmitype/Dataset',
    'http://purl.org/dc/dcmitype/Dataset#geographic',
}

# Mapeo de accrualPeriodicity (valores del yaml) → tolerancia máxima
# La tolerancia es el doble del período, igual que en tu función original.
# None = eventual → siempre vigente
PERIOD_TOLERANCE = {
    'http://publications.europa.eu/resource/authority/frequency/CONT': datetime.timedelta(days=30),
    'http://publications.europa.eu/resource/authority/frequency/HOURLY': datetime.timedelta(days=30),
    'http://publications.europa.eu/resource/authority/frequency/DAILY': datetime.timedelta(days=30),
    'http://publications.europa.eu/resource/authority/frequency/WEEKLY': datetime.timedelta(days=30),
    'http://publications.europa.eu/resource/authority/frequency/BIWEEKLY': datetime.timedelta(days=30),
    'http://publications.europa.eu/resource/authority/frequency/MONTHLY': datetime.timedelta(days=30),
    'http://publications.europa.eu/resource/authority/frequency/BIMONTHLY': datetime.timedelta(days=60),
    'http://publications.europa.eu/resource/authority/frequency/QUARTERLY': datetime.timedelta(days=90),
    'http://publications.europa.eu/resource/authority/frequency/ANNUAL_3': datetime.timedelta(days=120),
    'http://publications.europa.eu/resource/authority/frequency/ANNUAL_2': datetime.timedelta(days=180),
    'http://publications.europa.eu/resource/authority/frequency/ANNUAL': datetime.timedelta(days=365),
    'http://publications.europa.eu/resource/authority/frequency/BIENNIAL': datetime.timedelta(days=365 * 2),
    'http://publications.europa.eu/resource/authority/frequency/QUADRENNIAL': datetime.timedelta(days=365 * 4),
    'http://publications.europa.eu/resource/authority/frequency/DECENNIAL': datetime.timedelta(days=365 * 10),
    'http://publications.europa.eu/resource/authority/frequency/IRREG': None,  # eventual
}

# Valores de status del yaml
STATUS_VIGENTE = 'http://purl.org/adms/status/Completed'
STATUS_DESACTUALIZADO = 'http://purl.org/adms/status/Deprecated'
STATUS_SIN_MANTENIMIENTO = 'http://purl.org/adms/status/Withdrawn'


# ===========================================================================
# HELPERS
# ===========================================================================

def _parse_date(value):
    """Convierte string ISO o datetime a datetime. Retorna None si falla."""
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    try:
        # CKAN almacena fechas como strings ISO: "2024-03-15" o "2024-03-15T10:00:00"
        raw = str(value).strip()
        if 'T' in raw:
            return datetime.datetime.fromisoformat(raw)
        else:
            return datetime.datetime.strptime(raw, '%Y-%m-%d')
    except (TypeError, ValueError):
        return None


def _get_latest_modified(resources):
    """
    Recorre los recursos y retorna (latest_datetime, fuente) donde fuente es
    'dataset_type' si se encontró al menos un recurso de tipo Dataset,
    o 'fallback' si se usaron todos los recursos.

    Prioridad 1: recursos con distribution_type en DATASET_DISTRIBUTION_TYPES
    Prioridad 2: todos los recursos (fallback)

    Para cada recurso, el orden de campos a consultar es:
    last_modified → metadata_modified → created
    """
    DATE_FIELDS = ('last_modified', 'metadata_modified', 'created')

    def _best_date_for_resource(res):
        for field in DATE_FIELDS:
            parsed = _parse_date(res.get(field))
            if parsed:
                return parsed
        return None

    # Intento 1: solo recursos de tipo Dataset
    dataset_resources = [
        r for r in resources
        if r.get('distribution_type') in DATASET_DISTRIBUTION_TYPES
    ]

    if dataset_resources:
        dates = [_best_date_for_resource(r) for r in dataset_resources]
        dates = [d for d in dates if d]
        if dates:
            return max(dates), 'dataset_type'

    # Fallback: todos los recursos
    if resources:
        dates = [_best_date_for_resource(r) for r in resources]
        dates = [d for d in dates if d]
        if dates:
            return max(dates), 'fallback'

    return None, None


def _compute_status(last_modified_dt, accrual_periodicity):
    """
    Retorna el URI de status correspondiente según la lógica de tu función
    comp_mod_conperiod, adaptada a los valores URI del yaml.

    - eventual (IRREG) → siempre STATUS_VIGENTE
    - si (hoy - last_modified) > tolerancia * 2 → STATUS_SIN_MANTENIMIENTO
    - si (hoy - last_modified) > tolerancia     → STATUS_DESACTUALIZADO
    - si no                                      → STATUS_VIGENTE
    - si no hay last_modified                    → STATUS_VIGENTE (indeterminable)
    """
    if not accrual_periodicity:
        return STATUS_VIGENTE

    tolerance = PERIOD_TOLERANCE.get(accrual_periodicity)

    # eventual → siempre vigente
    if tolerance is None:
        return STATUS_VIGENTE

    if not last_modified_dt:
        # No hay fecha → indeterminable, dejamos vigente como comportamiento neutro
        return STATUS_VIGENTE

    today = datetime.datetime.now()
    # Normalizamos timezone: si last_modified tiene tz, quitamos
    if last_modified_dt.tzinfo is not None:
        last_modified_dt = last_modified_dt.replace(tzinfo=None)

    delta = today - last_modified_dt

    if delta > tolerance * 2:
        return STATUS_SIN_MANTENIMIENTO
    elif delta > tolerance:
        return STATUS_DESACTUALIZADO
    else:
        return STATUS_VIGENTE


def recalculate_dataset_fields(pkg_id):
    """
    Puede llamarse desde cualquier contexto: plugin, tarea asincrónica, CLI.
    Hace package_show, calcula y patchea si hay cambios.
    """
    pkg_dict = toolkit.get_action('package_show')(
        {'ignore_auth': True},
        {'id': pkg_id}
    )

    resources = pkg_dict.get('resources', [])
    latest_dt, fuente = _get_latest_modified(resources)
    dataset_modified = latest_dt.date().isoformat() if latest_dt else None
    accrual_periodicity = pkg_dict.get('dataset_accrualPeriodicity')
    new_status = _compute_status(latest_dt, accrual_periodicity)

    patch_data = {}
    if dataset_modified and dataset_modified != pkg_dict.get('dataset_modified'):
        patch_data['dataset_modified'] = dataset_modified
    if new_status and new_status != pkg_dict.get('dataset_status'):
        patch_data['dataset_status'] = new_status

    if patch_data:
        toolkit.get_action('package_patch')(
            {'ignore_auth': True, '__recalculating_dataset_fields': True},
            {'id': pkg_id, **patch_data}
        )
        log.info('Dataset %s actualizado: %s', pkg_id, patch_data)

    return patch_data