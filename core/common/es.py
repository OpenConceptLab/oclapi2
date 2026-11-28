class ESScript:
    # script: append the source version to the list field only if not already present (when given),
    # and optionally set is_in_latest_source_version -- avoids recomputing the full document (names,
    # synonyms, mapped codes, embeddings, etc.) when only source-version membership has changed.
    # Use case: Source Version resources indexing
    APPEND_SOURCE_VERSION_SCRIPT = """
        if (params.containsKey('version')) {
            if (ctx._source.source_version == null) { ctx._source.source_version = []; }
            if (!ctx._source.source_version.contains(params.version)) {
                ctx._source.source_version.add(params.version);
            }
        }
        if (params.containsKey('is_in_latest_source_version')) {
            ctx._source.is_in_latest_source_version = params.is_in_latest_source_version;
        }
        if (params.containsKey('verified')) {
            ctx._source.verified = params.verified;
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

    # script: set is_latest_version, put HEAD in source_version or take it out (params.in_head), leaving the other
    # versions as they are, and set the other fields in params.fields.
    # Use case: the documents of the versions an import superseded (OpenConceptLab/ocl_online#268)
    LATEST_FLAGS_SCRIPT = """
        ctx._source.is_latest_version = params.is_latest_version;
        def versions = ctx._source.source_version;
        if (versions == null) { versions = []; } else if (!(versions instanceof List)) { versions = [versions]; }
        versions.removeIf(version -> version == 'HEAD');
        if (params.in_head) { versions.add('HEAD'); }
        ctx._source.source_version = versions;
        for (entry in params.fields.entrySet()) { ctx._source[entry.getKey()] = entry.getValue(); }
    """
