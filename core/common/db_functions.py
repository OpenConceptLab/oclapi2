from django.db.models import Func, TextField


class ImmutableUnaccent(Func):  # pylint: disable=abstract-method
    # Immutable wrapper over unaccent (created in common/0004) so it can be used in index expressions.
    function = 'public.immutable_unaccent'
    output_field = TextField()
