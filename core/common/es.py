class ESScript:
    # script: append the new source version to the list field only if not already present,
    # and optionally set is_in_latest_source_version -- avoids recomputing the full document (names,
    # synonyms, mapped codes, embeddings, etc.) when only source-version membership has changed.
    # Use case: Source Version resources indexing
    APPEND_SOURCE_VERSION_SCRIPT = """
        if (ctx._source.source_version == null) { ctx._source.source_version = []; }
        if (!ctx._source.source_version.contains(params.version)) {
            ctx._source.source_version.add(params.version);
        }
        if (params.containsKey('is_in_latest_source_version')) {
            ctx._source.is_in_latest_source_version = params.is_in_latest_source_version;
        }
    """

    # script: append each value to the list field only if not already present
    # Use case: Expansion resources indexing
    APPEND_COLLECTION_FIELDS_SCRIPT = """
        for (entry in params.entrySet()) {
            def key = entry.getKey();
            def vals = entry.getValue();
            if (ctx._source[key] == null) { ctx._source[key] = []; }
            for (v in vals) {
                if (!ctx._source[key].contains(v)) { ctx._source[key].add(v); }
            }
        }
    """
