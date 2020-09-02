from django.conf import settings

from core.concepts.validators import BasicConceptValidator, ValidatorSpecifier


class ConceptValidationMixin:
    def clean(self):
        if settings.DISABLE_VALIDATION:
            return  # pragma: no cover

        validators = [BasicConceptValidator()]

        schema = self.parent.custom_validation_schema
        if schema:
            custom_validator = ValidatorSpecifier().with_validation_schema(
                schema
            ).with_repo(self.parent).with_reference_values().get()
            validators.append(custom_validator)

        for validator in validators:
            validator.validate(self)
