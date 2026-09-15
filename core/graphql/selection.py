"""Plan data access from the requested fields, including fragments and directives."""


def selected_paths(info):
    """Return selected leaf paths; aliases never change the underlying field names."""
    fields = getattr(info, 'selected_fields', None)
    if fields is None:
        return None
    paths = set()

    def walk(selections, prefix):
        """Expand Strawberry's resolved fragments and skip disabled selections."""
        for selection in selections:
            directives = getattr(selection, 'directives', {})
            if directives.get('skip', {}).get('if') or directives.get('include', {}).get('if') is False:
                continue
            name = getattr(selection, 'name', None)
            children = getattr(selection, 'selections', [])
            # Fragment spreads have a name too, but are not fields.
            is_field = type(selection).__name__ == 'SelectedField'
            path = prefix + (name,) if is_field else prefix
            if children:
                walk(children, path)
            elif is_field:
                paths.add('.'.join(path))

    for field in fields:
        walk(field.selections, ())
    return paths


def child_paths(paths, field):
    """Extract a nested object's requested leaves from a selection plan."""
    prefix = field + '.'
    return {path[len(prefix):] for path in paths if path.startswith(prefix)}


def index_projection(paths, fields):
    """Return the minimum index fields, or None when any leaf needs the ORM."""
    if paths is None or not paths <= fields.keys():
        return None
    return sorted({value for path in paths for value in fields[path]})
