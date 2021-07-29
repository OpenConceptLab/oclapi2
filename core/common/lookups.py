from django.db.models.lookups import In


class InValues(In):
    """
    A custom lookup to speed up IN queries.

    It converts the list of values into a temp table, if list larger than 100 items.
    """
    def get_rhs_op(self, connection, rhs):
        if self.rhs_is_direct_value() and len(self.rhs) > 100:
            return 'IN (SELECT unnest(%s))' % rhs
        return super().get_rhs_op(self, connection, rhs)
