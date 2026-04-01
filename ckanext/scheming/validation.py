import ast
import json
import datetime
from collections import defaultdict
import itertools

import shapely
from shapely.geometry import Polygon, shape, MultiPolygon
import logging

import pytz
import six

import ckan.lib.helpers as h
from ckan.lib.navl.dictization_functions import convert
from ckan.plugins.toolkit import (
    config,
    get_validator,
    UnknownValidator,
    missing,
    Invalid,
    StopOnError,
    _,
)

import ckanext.scheming.helpers as sh
from ckanext.scheming.errors import SchemingException

log = logging.getLogger(__name__)

OneOf = get_validator('OneOf')
ignore_missing = get_validator('ignore_missing')
not_empty = get_validator('not_empty')
unicode_safe = get_validator('unicode_safe')

all_validators = {}


def register_validator(fn):
    """
    collect validator functions into ckanext.scheming.all_helpers dict
    """
    all_validators[fn.__name__] = fn
    return fn


def scheming_validator(fn):
    """
    Decorate a validator that needs to have the scheming fields
    passed with this function. When generating navl validator lists
    the function decorated will be called passing the field
    and complete schema to produce the actual validator for each field.
    """
    fn.is_a_scheming_validator = True
    return fn


register_validator(unicode_safe)


@register_validator
def strip_value(value):
    '''
    **starting from CKAN 2.10 this is included in CKAN core**
    '''
    return value.strip()


@scheming_validator
@register_validator
def scheming_choices(field, schema):
    """
    Require that one of the field choices values is passed.
    """
    OneOf = get_validator('OneOf')
    if 'choices' in field:
        return OneOf([c['value'] for c in field['choices']])

    def validator(value):
        if value is missing or not value:
            return value
        choices = sh.scheming_field_choices(field)
        for choice in choices:
            if value == choice['value']:
                return value
        raise Invalid(_('unexpected choice "%s"') % value)

    return validator

@scheming_validator
@register_validator
def scheminggobar_geo_choices(field, schema):


    def validator(value):
        """
        Este validador puede usar un helper a definir que convierta el valor recibido (id o nombre) en un
        polígono o multipolígono y devuelva el mismo así como la URI de la entidad, se guardarían ambos
        """
        log.error(f"esto es lo que devuelve schemingobar_geo_choices{value}")
        return value


    return validator

@scheming_validator
@register_validator
def scheming_required(field, schema):
    """
    return a validator based on field['required']
    and schema['draft_fields_required'] setting
    """
    if not field.get('required'):
        return get_validator('ignore_missing')
    if not schema.get('draft_fields_required', True):
        return get_validator('scheming_draft_fields_not_required')
    return get_validator('not_empty')


@register_validator
def scheming_draft_fields_not_required(key, data, errors, context):
    """
    call ignore_missing if state is draft, otherwise not_empty
    """
    state = data.get(('state',), missing)
    if state is missing or state.startswith('draft'):
        v = get_validator('ignore_missing')
    else:
        v = get_validator('not_empty')
    v(key, data, errors, context)


@scheming_validator
@register_validator
def scheming_multiple_choice(field, schema):
    """
    Accept zero or more values from a list of choices and convert
    to a json list for storage:

    1. a list of strings, eg.:

       ["choice-a", "choice-b"]

    2. a single string for single item selection in form submissions:

       "choice-a"
    """
    static_choice_values = None
    if 'choices' in field:
        static_choice_order = [c['value'] for c in field['choices']]
        static_choice_values = set(static_choice_order)

    def validator(key, data, errors, context):
        # if there was an error before calling our validator
        # don't bother with our validation
        if errors[key]:
            return

        value = data[key]
        if value is not missing:
            if isinstance(value, six.string_types):
                value = [value]
            elif not isinstance(value, list):
                errors[key].append(_('expecting list of strings'))
                return
        else:
            value = []

        choice_values = static_choice_values
        if not choice_values:
            choice_order = [
                choice['value']
                for choice in sh.scheming_field_choices(field)
            ]
            choice_values = set(choice_order)

        selected = set()
        for element in value:
            if element in choice_values:
                selected.add(element)
                continue
            errors[key].append(_('unexpected choice "%s"') % element)

        if not errors[key]:
            data[key] = json.dumps([
                v for v in
                (static_choice_order if static_choice_values else choice_order)
                if v in selected
            ])

            really_required = schema.get('draft_fields_required', True
                ) or not data.get(('state',), 'draft').startswith('draft')
            if not selected and field.get('required') and really_required:
                errors[key].append(_('Select at least one'))

    return validator


def validate_date_inputs(field, key, data, extras, errors, context):
    date_error = _('Date format incorrect')
    time_error = _('Time format incorrect')

    date = None

    def get_input(suffix):
        inpt = key[0] + '_' + suffix
        new_key = (inpt,) + tuple(x for x in key if x != key[0])
        key_value = extras.get(inpt)
        data[new_key] = key_value
        errors[new_key] = []

        if key_value:
            del extras[inpt]

        if field.get('required'):
            not_empty(new_key, data, errors, context)

        return new_key, key_value

    date_key, value = get_input('date')
    value_full = ''

    if value:
        try:
            value_full = value
            date = h.date_str_to_datetime(value)
        except (TypeError, ValueError) as e:
            errors[date_key].append(date_error)

    time_key, value = get_input('time')
    if value:
        if not value_full:
            errors[date_key].append(
                _('Date is required when a time is provided'))
        else:
            try:
                value_full += ' ' + value
                date = h.date_str_to_datetime(value_full)
            except (TypeError, ValueError) as e:
                errors[time_key].append(time_error)

    tz_key, value = get_input('tz')
    if value:
        if value not in pytz.all_timezones:
            errors[tz_key].append('Invalid timezone')
        else:
            if isinstance(date, datetime.datetime):
                date = pytz.timezone(value).localize(date)

    return date
#VALIDADORES DE SCHEMINGDCAT PARA TEMPORAL COVERAGE
@scheming_validator
@register_validator
def schemingdcat_fill_dependent_fields(field, schema):
    """
    Validator that fills dependent fields based on the value of the primary field.

    This validator checks if the primary field has a value and, if so, fills the dependent fields
    specified in the `dependent_fields` attribute of the primary field. If the dependent fields
    have subfields, it will also fill those subfields with the same value.

    Args:
        field (dict): The field definition containing the `dependent_fields` attribute.
        schema (dict): The schema definition.

    Returns:
        function: A validator function that processes the dependent fields.

    Validator Args:
        key (tuple): The key of the field being validated.
        data (dict): The data dictionary containing all field values.
        errors (dict): The dictionary to collect validation errors.
        context (dict): The context dictionary containing additional information.

    Raises:
        None: This validator does not raise exceptions but logs errors if they occur.
    """

    # log.debug('field.dependent_fields: %s', field.get('dependent_fields'))
    def validator(key, data, errors, context):
        dependent_fields = field.get('dependent_fields')

        if not dependent_fields:
            return validator

        value = data.get(key)

        if value in (missing, None, ''):
            data[key] = None
            return validator

        dependent_field_name = dependent_fields['field_name']

        schemingdcat_fill_subfields(dependent_field_name, dependent_fields, value, data)

    return validator


# Aux function to fill subfields for schemingdcat fill_dependent_fields validators
def schemingdcat_fill_subfields(dependent_field_name, dependent_fields, value, data):
    """
    Fills subfields for schemingdcat fill_dependent_fields validators.

    Args:
        dependent_field_name (str): The name of the dependent field.
        dependent_fields (dict): The dictionary containing subfields information.
        value (any): The value to be set for the subfields.
        data (dict): The data dictionary where the subfields will be set.

    Returns:
        None

    Raises:
        IndexError: If there is an indexing error while setting the subfield value.
        ValueError: If there is a value error while setting the subfield value.
        KeyError: If there is a key error while setting the subfield value.
    """
    dependent_key = (dependent_field_name,)
    subfields = dependent_fields.get('subfields')

    if subfields:
        for subfield in subfields:
            subfield_name = subfield['field_name']
            if value:
                dependent_subkey = (dependent_field_name, 0, subfield_name)
                try:
                    data[dependent_subkey] = value
                except (IndexError, ValueError, KeyError) as e:
                    log.error('Exception occurred while setting subfield value: %s', e)
    else:
        if value:
            try:
                data[dependent_key] = value
            except (IndexError, ValueError, KeyError) as e:
                log.error('Exception occurred while setting field value: %s', e)

## VALIDADORES AGREGADOS DE SCHEMING DCAT PARA SPATIAL:

#schemingdcat_spatial_uri_validator
#schemingdcat_fill_spatial_uri_dependent_fields
#schemingdcat_fill_spatial_dependent_fields


@scheming_validator
@register_validator
def scheminggobar_spatial_uri_validator(field, schema):
    """
    Inspired by schemingdcat_spatial_uri_validator
    Scheming validator for the ``spatial`` field (bounding-box / GeoJSON Polygon).

    Auto-derives the spatial extent of a dataset from the provincial URI(s) stored
    in ``spatial_uri``, removing the need for manual GeoJSON entry when the
    geographic coverage can be inferred from the selection.

    Behaviour
    ---------
    - If ``spatial`` already contains a value (user entered it manually), the
      validator exits immediately without making any changes.
    - If ``spatial`` is blank, it is filled automatically based on ``spatial_uri``:

      * **No selection** → field is set to ``missing`` (left empty).
      * **Single URI** → the GeoJSON Polygon stored in that choice's ``spatial``
        key is assigned directly, preserving the original provincial boundary.
      * **Multiple URIs** → the polygons of all matched choices are merged via
        ``shapely.ops.unary_union`` and the **axis-aligned bounding box** of the
        resulting geometry is stored as a GeoJSON Polygon.  This bbox is always a
        simple rectangular Polygon regardless of whether the underlying union is
        contiguous or a MultiPolygon.

    Error handling
    --------------
    - If ``unary_union`` or the bbox calculation fails for any reason, the
      validator logs the error and falls back to the raw polygon string of the
      first matched choice.
    - If a URI in the list has no corresponding choice (or the choice has no
      ``spatial`` key), it is silently skipped; only matched polygons participate
      in the union.

    Internal helpers
    ----------------
    _uri_list_from_value(value)
        Normalises the raw value of ``spatial_uri`` into a plain Python list of
        URI strings.  Accepts three input formats:

        - Python list:          ``["https://...BA", "https://...CAT"]``
        - JSON-encoded string:  ``'["https://...BA", "https://...CAT"]'``
        - Single URI string:    ``"https://...BA"``

        Returns ``[]`` for ``None``, ``missing``, or empty string.

    _bbox_polygon_from_union(polygons)
        Receives a list of shapely geometry objects, computes their union, and
        returns the bounding box as a GeoJSON Polygon dict with five coordinate
        pairs (the first repeated to close the ring):

        .. code-block:: python

            {
                "type": "Polygon",
                "coordinates": [[
                    [minx, miny], [maxx, miny], [maxx, maxy],
                    [minx, maxy], [minx, miny]
                ]]
            }

    Args:
        field (dict): The scheming field definition.  Not used directly but
            required by the ``@scheming_validator`` protocol.
        schema (dict): The full dataset schema dict.  Used to look up the
            ``spatial_uri`` field and its ``choices`` (each choice may carry a
            ``spatial`` key with a GeoJSON Polygon string).

    Returns:
        function: A navl-style validator ``(key, data, errors, context) → None``
            that modifies ``data[key]`` in place.
    """
    schema_data = sh.scheming_get_dataset_schema(schema['dataset_type'])
    spatial_uri_field = next(
        (f for f in schema_data['dataset_fields'] if f['field_name'] == 'spatial_uri'),
        None
    )
    choices = spatial_uri_field['choices'] if spatial_uri_field else []

    def _uri_list_from_value(value):
        """Normalise whatever spatial_uri holds into a plain list of strings."""
        if value in (missing, None, ''):
            return []
        if isinstance(value, list):
            return [v for v in value if v]
        if isinstance(value, six.string_types):
            stripped = value.strip()
            if stripped.startswith('['):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [v for v in parsed if v]
                except (ValueError, TypeError):
                    pass
            return [stripped] if stripped else []
        return []

    def _polygon_from_union(polygons):
        """
        Given a list of shapely geometries return the axis-aligned bounding-box
        as a GeoJSON Polygon dict.
        """
        from shapely.ops import unary_union
        try:
            union = unary_union(polygons)
            geoj = shapely.to_geojson(union)
            log.error(f"geoj es un objeto de tipo: {type(geoj)}")


            #minx, miny, maxx, maxy = union.bounds
        except Exception as e:
            log.error('schemingdcat_spatial_uri_validator: union failed: %s', e)
            # fall back to bounds of the first polygon
            #minx, miny, maxx, maxy = polygons[0].bounds
            geoj = None

        return geoj

    def validator(key, data, errors, context):
        # Only auto-fill when the field is blank
        if data[key] not in (missing, None, ''):
            return

        spatial_uri_raw = data.get(('spatial_uri',))
        uri_list = _uri_list_from_value(spatial_uri_raw)

        if not uri_list:
            data[key] = missing
            return

        # Collect the GeoJSON polygon strings for every matched URI
        matched_spatial = []
        for uri in uri_list:
            choice = next((c for c in choices if c.get('value') == uri), None)
            if choice and choice.get('spatial'):
                matched_spatial.append(choice['spatial'])

        if not matched_spatial:
            data[key] = missing
            return

        if len(matched_spatial) == 1:
            # Single province: use the stored polygon as-is
            data[key] = matched_spatial[0]
            return

        # Multiple provinces: compute the union bbox
        try:
            polygons = [shape(json.loads(s)) for s in matched_spatial]
            bbox_geojson = _polygon_from_union(polygons)
            #data[key] = json.dumps(bbox_geojson)
            data[key] = bbox_geojson
        except Exception as e:
            log.error('scheminggobar_spatial_uri_validator: could not build union bbox: %s', e)
            # Last resort: use the first polygon
            data[key] = matched_spatial[0]

    return validator


@scheming_validator
@register_validator
def scheminggobar_fill_spatial_uri_dependent_fields(field, schema):
    """
    Inspired by schemingdcat_fill_spatial_uri_dependent_fields
    Scheming validator for the ``spatial_uri`` field (provincial selector).

    Populates the repeating subfields of ``spatial_coverage`` from the URI(s)
    chosen in the provincial selector, and serialises the field value for storage.
    Works for both single and multiple province selection.

    Behaviour
    ---------
    - If the field value is empty (``None``, ``missing``, or blank string),
      ``data[key]`` is set to ``None`` and the validator returns early.
    - For each valid URI in the selection, one entry is written into
      ``spatial_coverage`` using a sequential integer index (0, 1, 2, …):

      * ``(spatial_coverage, idx, "uri")``  ← the raw URI string.
      * ``(spatial_coverage, idx, "text")`` ← the human-readable label resolved
        from the choice's ``label`` key, respecting the active CKAN locale.

    - URIs that are not found in the field's ``choices`` list are skipped with a
      ``WARNING`` log entry; they do not interrupt processing of the remaining URIs.
    - At the end, ``data[key]`` is always serialised as a **JSON list** (e.g.
      ``'["uri1"]'`` for a single selection, ``'["uri1","uri2"]'`` for multiple).
      The companion ``output_validators: scheming_multiple_choice_output`` defined
      in the ``select_geo_data`` preset deserialises this back to a Python list
      at read time, so the Jinja template expression ``val in data[field_name]``
      performs a proper membership check instead of a substring search.

    Input formats accepted
    ----------------------
    The validator normalises the raw field value before processing.  All three
    formats that CKAN/WebOb may produce are handled:

    - **Python list** (``<select multiple>`` via WebOb POST):
      ``["https://...BA", "https://...CAT"]``
    - **JSON-encoded string** (value read back from storage):
      ``'["https://...BA", "https://...CAT"]'``
    - **Plain string** (single selection or legacy data):
      ``"https://...BA"``

    Internal helpers
    ----------------
    _resolve_label(choice)
        Returns the display string for a choice, trying the active locale first,
        then ``'es'``, then the first available value in the label dict.
        If ``label`` is already a plain string it is returned as-is.

    _normalize_value(value)
        Converts any of the three input formats above into a plain Python list
        of non-empty URI strings.  Returns ``[]`` for absent or blank input.

    Args:
        field (dict): The scheming field definition.  Must contain ``choices``
            (list of dicts with ``value``, ``label``) and ``dependent_fields``
            (dict with ``field_name`` pointing to ``spatial_coverage``).
        schema (dict): The full dataset schema dict.  Not used directly but
            required by the ``@scheming_validator`` protocol.

    Returns:
        function: A navl-style validator ``(key, data, errors, context) → None``
            that modifies ``data`` in place.
    """
    lang = config.get('ckan.locale_default', 'en')
    spatial_uri_choices = field['choices'] if field else []

    def _resolve_label(choice):
        """Return the display label for a choice dict, respecting the active locale."""
        label = choice.get('label')
        if isinstance(label, dict):
            return label.get(lang) or label.get('es') or next(iter(label.values()), '')
        return label or ''

    def _normalize_value(value):
        """
        Always return a list of URI strings regardless of how the value arrived
        (single string, JSON-encoded list, or Python list).
        Returns an empty list when the value is absent or blank.
        """
        if value in (missing, None, ''):
            return []
        if isinstance(value, list):
            return [v for v in value if v]
        if isinstance(value, six.string_types):
            stripped = value.strip()
            if stripped.startswith('['):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, list):
                        return [v for v in parsed if v]
                except (ValueError, TypeError):
                    pass
            # single URI string
            return [stripped] if stripped else []
        return []

    def validator(key, data, errors, context):
        dependent_fields = field.get('dependent_fields')
        if not dependent_fields:
            return

        raw_value = data.get(key)
        uri_list = _normalize_value(raw_value)

        if not uri_list:
            data[key] = None
            return

        dependent_field_name = dependent_fields['field_name']

        for idx, uri in enumerate(uri_list):
            value_choice = next(
                (item for item in spatial_uri_choices if item.get('value') == uri),
                None
            )
            if not value_choice:
                log.warning('schemingdcat_fill_spatial_uri_dependent_fields: URI not found in choices: %s', uri)
                continue

            spatial_text_value = _resolve_label(value_choice)

            subfields_to_write = {
                'uri': uri,
                'text': spatial_text_value,
            }

            for subfield_name, subfield_value in subfields_to_write.items():
                dependent_subkey = (dependent_field_name, idx, subfield_name)
                try:
                    data[dependent_subkey] = subfield_value
                except (IndexError, ValueError, KeyError) as e:
                    log.error(
                        'schemingdcat_fill_spatial_uri_dependent_fields: '
                        'error writing %s[%d].%s = %r: %s',
                        dependent_field_name, idx, subfield_name, subfield_value, e
                    )

        # Serialise to a JSON list for storage.
        # scheming_multiple_choice_output (set as output_validator in the preset)
        # will deserialise this back to a Python list when the field is read,
        # so the display template sees a list and 'val in data[field_name]' works.
        # Single-value case also uses a JSON list for consistency: ["uri"].
        data[key] = json.dumps(uri_list)

    return validator


@scheming_validator
@register_validator
def schemingdcat_fill_spatial_dependent_fields(field, schema):
    """
    Fills spatial dependent fields such as centroid and bounding box based on the provided GeoJSON value.

    Args:
        field (dict): The field definition containing the dependent fields information.
        schema (dict): The schema definition.

    Returns:
        function: A validator function that processes the GeoJSON value and fills the dependent fields.

    Raises:
        json.JSONDecodeError: If the provided value is not a valid JSON.
        ValueError: If the provided GeoJSON does not represent a Polygon.
    """

    def validator(key, data, errors, context):
        dependent_fields = field.get('dependent_fields')

        if not dependent_fields:
            return validator

        value = data.get(key)

        if value in (missing, None, ''):
            data[key] = None
            return validator

        try:
            value_dict = json.loads(value)
            polygon = shape(value_dict)
            if not isinstance(polygon, Polygon) and not isinstance(polygon, MultiPolygon):
                raise ValueError("The provided GeoJSON does not represent a Polygon.")
        except (json.JSONDecodeError, ValueError) as e:
            log.error('Invalid GeoJSON value: %s', e)
            errors[key].append('Invalid GeoJSON value.')
            return validator

        centroid = polygon.centroid
        centroid_value = {
            "type": "Point",
            "coordinates": [round(centroid.x, 5), round(centroid.y, 5)]
        }

        subfields = {
            'centroid': centroid_value,
            'bbox': value_dict
        }

        for subfield_name, subfield_value in subfields.items():
            subfield_dict = {
                'field_name': dependent_fields['field_name'],
                'subfields': [{'field_name': subfield_name}]
            }
            schemingdcat_fill_subfields(dependent_fields['field_name'], subfield_dict, subfield_value, data)

    return validator


### FIN DE AGREGADOS DE SCHEMINGDCAT



#FIN DE FUNCIONES PARA CAMPOS ESPACIALES

@scheming_validator
@register_validator
def scheming_isodatetime(field, schema):
    def validator(key, data, errors, context):
        value = data[key]
        date = None

        if value:
            if isinstance(value, datetime.datetime):
                return value
            else:
                try:
                    date = h.date_str_to_datetime(value)
                except (TypeError, ValueError) as e:
                    raise Invalid(_('Date format incorrect'))
        else:
            extras = data.get(('__extras',))
            if not extras or (key[0] + '_date' not in extras and
                              key[0] + '_time' not in extras):
                if field.get('required'):
                    not_empty(key, data, errors, context)
            else:
                date = validate_date_inputs(
                    field, key, data, extras, errors, context)

        data[key] = date

    return validator


@scheming_validator
@register_validator
def scheming_isodatetime_tz(field, schema):
    def validator(key, data, errors, context):
        value = data[key]
        date = None

        if value:
            if isinstance(value, datetime.datetime):
                date = sh.scheming_datetime_to_utc(value)
            else:
                try:
                    date = sh.date_tz_str_to_datetime(value)
                except (TypeError, ValueError) as e:
                    raise Invalid(_('Date format incorrect'))
        else:
            if 'resources' in key and len(key) > 1:
                # when a resource is edited, extras will be under a different key in the data
                extras = data.get((('resources', key[1], '__extras')))
                # the key for the current field also looks different for a resource,
                # for example, a dataset might have the key ('start_timestamp')
                # for a resource this might look like ('resources', 3, 'start_timestamp')
                # however, we need to pass on a tuple with just the field name
                field_name_index_in_key = 2

            else:
                extras = data.get(('__extras',))
                field_name_index_in_key = 0

            if not extras or (
                (
                    key[field_name_index_in_key] + '_date' not in extras
                    and key[field_name_index_in_key] + '_time' not in extras
                )
            ):
                if field.get('required'):
                    not_empty(key, data, errors, context)
            else:
                date = validate_date_inputs(
                    field=field,
                    key=(key[field_name_index_in_key],),
                    data=data,
                    extras=extras,
                    errors=errors,
                    context=context,
                )
                if isinstance(date, datetime.datetime):
                    date = sh.scheming_datetime_to_utc(date)

        data[key] = date

    return validator


@register_validator
def scheming_valid_json_object(value, context):
    """Store a JSON object as a serialized JSON string

    It accepts two types of inputs:
        1. A valid serialized JSON string (it must be an object or a list)
        2. An object that can be serialized to JSON

    """
    if not value:
        return
    elif isinstance(value, six.string_types):
        try:
            loaded = json.loads(value)

            if not isinstance(loaded, dict):
                raise Invalid(
                    _('Unsupported value for JSON field: {}').format(value)
                )

            return value
        except (ValueError, TypeError) as e:
            raise Invalid(_('Invalid JSON string: {}').format(e))

    elif isinstance(value, dict):
        try:
            return json.dumps(value)
        except (ValueError, TypeError) as e:
            raise Invalid(_('Invalid JSON object: {}').format(e))
    else:
        raise Invalid(
            _('Unsupported type for JSON field: {}').format(type(value))
        )


@register_validator
def scheming_load_json(value, context):
    if isinstance(value, six.string_types):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


@register_validator
def scheming_multiple_choice_output(value):
    """
    return stored json as a proper list
    """
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return [value]


def validators_from_string(s, field, schema):
    """
    convert a schema validators string to a list of validators

    e.g. "if_empty_same_as(name) unicode_safe" becomes:
    [if_empty_same_as("name"), unicode_safe]
    """
    out = []
    parts = s.split()
    for p in parts:
        if '(' in p and p[-1] == ')':
            name, args = p.split('(', 1)
            args = args[:-1]  # trim trailing ')'
            try:
                parsed_args = ast.literal_eval(args)
                if not isinstance(parsed_args, tuple) or not parsed_args:
                    # it's a signle argument. `not parsed_args` means that this single
                    # argument is an empty tuple, for example: "default(())"
                    parsed_args = (parsed_args,)

            except (ValueError, TypeError, SyntaxError, MemoryError):
                parsed_args = args.split(',')

            v = get_validator_or_converter(name)(*parsed_args)
        else:
            v = get_validator_or_converter(p)
        if getattr(v, 'is_a_scheming_validator', False):
            v = v(field, schema)
        out.append(v)
    return out


def get_validator_or_converter(name):
    """
    Get a validator or converter by name
    """
    if name == 'unicode':
        return six.text_type
    try:
        v = get_validator(name)
        return v
    except UnknownValidator:
        pass
    raise SchemingException('validator/converter not found: %r' % name)


def convert_from_extras_group(key, data, errors, context):
    """Converts values from extras, tailored for groups."""

    def remove_from_extras(data, key):
        to_remove = []
        for data_key, data_value in data.items():
            if (data_key[0] == 'extras'
                    and data_key[1] == key):
                to_remove.append(data_key)
        for item in to_remove:
            del data[item]

    for data_key, data_value in data.items():
        if (data_key[0] == 'extras'
            and 'key' in data_value
                and data_value['key'] == key[-1]):
            data[key] = data_value['value']
            break
    else:
        return
    remove_from_extras(data, data_key[1])


@register_validator
def convert_to_json_if_date(date, context):
    if isinstance(date, datetime.datetime):
        return date.date().isoformat()
    elif isinstance(date, datetime.date):
        return date.isoformat()
    else:
        return date


@register_validator
def convert_to_json_if_datetime(date, context):
    if isinstance(date, datetime.datetime):
        return date.isoformat()

    return date


@scheming_validator
@register_validator
def scheming_multiple_text(field, schema):
    """
    Accept repeating text input in the following forms and convert to a json list
    for storage. Also act like scheming_required to check for at least one non-empty
    string when required is true:

    1. a list of strings, eg.

       ["Person One", "Person Two"]

    2. a single string value to allow single text fields to be
       migrated to repeating text

       "Person One"
    """
    def _scheming_multiple_text(key, data, errors, context):
        # just in case there was an error before our validator,
        # bail out here because our errors won't be useful
        if errors[key]:
            return

        value = data[key]
        # 1. list of strings or 2. single string
        if value is not missing:
            if isinstance(value, six.string_types):
                value = [value]
            if not isinstance(value, list):
                errors[key].append(_('expecting list of strings'))
                raise StopOnError

            out = []
            for element in value:
                if not element:
                    continue

                if not isinstance(element, six.string_types):
                    errors[key].append(_('invalid type for repeating text: %r')
                                       % element)
                    continue
                if isinstance(element, six.binary_type):
                    try:
                        element = element.decode('utf-8')
                    except UnicodeDecodeError:
                        errors[key]. append(_('invalid encoding for "%s" value')
                                            % element)
                        continue

                out.append(element)

            if errors[key]:
                raise StopOnError

            data[key] = json.dumps(out)

        if (data[key] is missing or data[key] == '[]'):
            if field.get('required'):
                errors[key].append(_('Missing value'))
                raise StopOnError
            data[key] = '[]'

    return _scheming_multiple_text


@register_validator
def repeating_text_output(value):
    """
    Return stored json representation as a list, if
    value is already a list just pass it through.
    """
    if isinstance(value, list):
        return value
    if value is None:
        return []
    try:
        return json.loads(value)
    except ValueError:
        return [value]
