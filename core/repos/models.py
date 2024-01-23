from core.collections.models import Collection
from core.sources.models import Source


class Repository:
    @classmethod
    def get(cls, criteria):
        repo = Source.objects.filter(criteria).first()

        if not repo:
            repo = Collection.objects.filter(criteria).first()

        return repo
