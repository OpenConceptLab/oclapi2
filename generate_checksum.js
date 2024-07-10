const crypto = require('crypto');
const { program } = require('commander');

function genericSort(list) {
  return list.sort((a, b) => {
    if (typeof a === 'string' || typeof b === 'string') {
      return a.localeCompare(b);
    }
    return a - b;
  });
}

function jsonStringifyWithSpaces(obj) {
  const jsonString = JSON.stringify(obj, null, 2);
  return jsonString.replace(/,\n\s+/g, ', ');
}

function customStringify(obj) {
  return JSON.stringify(obj, (key, value) => {
    if (Array.isArray(value)) {
      return value.join(', ');
    }
    return value;
  }, 2).replace(/", /g, '", ');
}

function formatArrayWithSpaces(array) {
  return `[${array.map(item => `"${item}"`).join(', ')}]`;
}

function serialize(obj) {
  if (Array.isArray(obj) && obj.length === 1) {
    obj = obj[0];
  }
  if (Array.isArray(obj)) {
    return `[${genericSort(obj).map(serialize).join(',')}]`;
  }
  if (typeof obj === 'object' && obj !== null) {
    const keys = genericSort(Object.keys(obj));
    let acc = `{${formatArrayWithSpaces(keys)}`;
    keys.forEach(key => {
      acc += `${serialize(obj[key])},`;
    });
    return `${acc}}`;
  }
  if (typeof obj === 'string' && obj.match(/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/)) {
    return JSON.stringify(obj);
  }
  return JSON.stringify(obj);
}

function cleanup(fields) {
  let result = fields;
  if (typeof fields === 'object' && fields !== null) {
    result = {};
    for (const key in fields) {
      let value = fields[key];
      if (value === null) continue;
      if (['retired', 'parent_concept_urls', 'child_concept_urls', 'descriptions', 'extras', 'names'].includes(key) && !value)
       continue;
      if (key === 'is_active' && value) continue;
      if(typeof(value) === 'number' && parseInt(value) === parseFloat(value))
        value = parseInt(value);
      if (key === 'extras') {
        if (typeof value === 'object' && Object.keys(value).some(k => k.startsWith('__'))) {
          const valueCopied = { ...value };
          for (const extraKey in value) {
            if (extraKey.startsWith('__')) {
              delete valueCopied[extraKey];
            }
          }
          value = valueCopied;
        }
      }
      result[key] = value;
    }
  }
  return result;
}

function localesForChecksums(data, relation, fields, predicateFunc) {
  const locales = data[relation] || [];
  return locales.map(locale => {
    const result = {};
    fields.forEach(field => {
      result[field] = locale[field] || null;
    });
    return predicateFunc(locale) ? result : null;
  }).filter(locale => locale !== null);
}

function generate(obj, hashAlgorithm = 'md5') {
  const serializedObj = serialize(obj);
  console.log()
  console.log("After Serialization:")
  console.log(serializedObj)
  const hash = crypto.createHash(hashAlgorithm);
  hash.update(serializedObj);
  return hash.digest('hex');
}

function isFullySpecifiedType(type) {
  if (!type) return false;
  if (type === 'FULLY_SPECIFIED' || type === 'Fully Specified') return true;
  type = type.replace(/\s|[-_]/g, '').toLowerCase();
  return type === 'fullyspecified';
}

function convertEmptyObjectToNull(obj) {
  if (obj && Object.keys(obj).length === 0 && obj.constructor === Object) {
    return null;
  }
  return obj;
}

function getConceptFields(data, checksumType) {
  const nameFields = ['locale', 'locale_preferred', 'name', 'name_type'];
  const descriptionFields = ['locale', 'locale_preferred', 'description', 'description_type'];

  if (checksumType === 'standard') {
    return {
      concept_class: data.concept_class || null,
      datatype: data.datatype || null,
      retired: data.retired || false,
      external_id: data.external_id || null,
      extras: convertEmptyObjectToNull(data.extras || null),
      names: localesForChecksums(data, 'names', nameFields, () => true),
      descriptions: localesForChecksums(data, 'descriptions', descriptionFields, () => true),
      parent_concept_urls: data.parent_concept_urls || [],
      child_concept_urls: data.child_concept_urls || []
    };
  }

  return {
    concept_class: data.concept_class || null,
    datatype: data.datatype || null,
    retired: data.retired || false,
    names: localesForChecksums(data, 'names', nameFields, locale => isFullySpecifiedType(locale.name_type))
  };
}

function getMappingFields(data, checksumType) {
  const fields = {
    map_type: data.map_type || null,
    from_concept_code: data.from_concept_code || null,
    to_concept_code: data.to_concept_code || null,
    from_concept_name: data.from_concept_name || null,
    to_concept_name: data.to_concept_name || null,
    retired: data.retired || false
  };

  if (checksumType === 'standard') {
    return {
      ...fields,
      sort_weight: parseFloat(data.sort_weight || 0) || null,
      extras: convertEmptyObjectToNull(data.extras || null),
      external_id: data.external_id || null,
      from_source_url: data.from_source_url || null,
      from_source_version: data.from_source_version || null,
      to_source_url: data.to_source_url || null,
      to_source_version: data.to_source_version || null
    };
  }

  return fields;
}

function flatten(inputList, depth = 1) {
  return inputList.reduce((acc, item) => {
    if (Array.isArray(item) && depth > 0) {
      acc.push(...flatten(item, depth - 1));
    } else {
      acc.push(item);
    }
    return acc;
  }, []);
}

function generateChecksum(resource, data, checksumType = 'standard') {
  if (!resource || !['concept', 'mapping'].includes(resource.toLowerCase())) {
    throw new Error(`Invalid resource: ${resource}`);
  }
  if (!['standard', 'smart'].includes(checksumType)) {
    throw new Error(`Invalid checksum type: ${checksumType}`);
  }

  if (resource === 'concept') {
    data = flatten([data]).map(d => getConceptFields(d, checksumType));
  } else if (resource === 'mapping') {
    data = flatten([data]).map(d => getMappingFields(d, checksumType));
  }

  console.log()
  console.log("Fields for Checksum:")
  console.log(data)

  console.log()
  console.log("After Cleanup:")
  console.log(data.map(d => cleanup(d)))


  const checksums = Array.isArray(data)
    ? data.map(d => generate(cleanup(d)))
    : [generate(cleanup(data))];

  if (checksums.length === 1) {
    return checksums[0];
  }

  return generate(checksums);
}

// CLI usage with commander
program
  .requiredOption('-r, --resource <type>', 'The type of resource (concept, mapping)')
  .requiredOption('-c, --checksum_type <type>', 'The type of checksum to generate (default: standard)', 'standard')
  .requiredOption('-d, --data <json>', 'The data for which checksum needs to be generated')

program.parse(process.argv);

const options = program.opts();

try {
  const data = JSON.parse(options.data);
  const checksum = generateChecksum(options.resource, data, options.checksum_type);
  console.log()
  console.log('\x1b[6;30;42m' + `${options.checksum_type.charAt(0).toUpperCase() + options.checksum_type.slice(1)} Checksum: ${checksum}` + '\x1b[0m');
  console.log()
} catch (error) {
  console.error('Error:', error.message);
}
