import logging

from django.db.models import F
from rest_framework.exceptions import ValidationError, NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.bundles.serializers import FHIRBundleSerializer
from core.code_systems.serializers import CodeSystemDetailSerializer, \
    ValidateCodeParametersSerializer
from core.common.constants import HEAD
from core.common.fhir_helpers import translate_fhir_query
from core.common.serializers import IdentifierSerializer
from core.concepts.permissions import CanViewParentDictionaryAsGuest
from core.concepts.views import ConceptRetrieveUpdateDestroyView
from core.fhir.content_negotiation import IgnoreClientContentNegotiation
from core.parameters.serializers import ParametersSerializer
from core.sources.models import Source
from core.sources.views import SourceListView, SourceRetrieveUpdateDestroyView

logger = logging.getLogger('oclapi')


class CapabilityStatementView(APIView):
    content_negotiation_class = IgnoreClientContentNegotiation
    permission_classes = (AllowAny,)

    def get(self, request):
        mode = request.query_params.get('mode')
        if mode == 'terminology':
            response = {
                "resourceType" : "TerminologyCapabilities",
                "id" : "example",
                "text" : {
                    "status" : "generated",
                    "div" : "<div xmlns=\"http://www.w3.org/1999/xhtml\">\n\t\t\t\n      <p>The OCL Server supports the following transactions for the resource Person: read, vread, \n        update, history, search(name,gender), create and updates.</p>\n\t\t\t\n      <p>The EHR System supports the following message: admin-notify::Person.</p>\n\t\t\t\n      <p>The EHR Application has a \n        \n        <a href=\"http://fhir.hl7.org/base/Profilebc054d23-75e1-4dc6-aca5-838b6b1ac81d/_history/b5fdd9fc-b021-4ea1-911a-721a60663796\">general document profile</a>.\n      \n      </p>\n\t\t\n    </div>"
                },
                "url" : "urn:uuid:68d043b5-9ecf-4559-a57a-396e0d452311",
                "identifier" : [{
                    "system" : "urn:ietf:rfc:3986",
                    "value" : "urn:oid:2.16.840.1.113883.4.642.6.2"
                }],
                "version" : "20130510",
                "name" : "OCL",
                "title" : "OCL capability statement",
                "status" : "draft",
                "experimental" : True,
                "date" : "2012-01-04",
                "publisher" : "OCL",
                "contact" : [{
                    "name" : "System Administrator",
                    "telecom" : [{
                        "system" : "email",
                        "value" : "admin@openconceptlab.org"
                    }]
                }],
                "description" : "This is the FHIR capability statement for the main EHR at ACME for the private interface - it does not describe the public interface",
                "kind" : "instance",
                "software" : {
                    "name" : "TxServer",
                    "version" : "0.1.2"
                },
                "implementation" : {
                    "description" : "OCL Terminology Server",
                    "url" : "http://example.org/tx"
                },
                "codeSearch" : "in-compose-or-expansion"
            }
        else:
            response = {
                "resourceType" : "CapabilityStatement",
                "id" : "openconceptlab-terminology-server",
                "text" : {
                    "status" : "extensions",
                    "div" : "<div xmlns=\"http://www.w3.org/1999/xhtml\">\n      <h2>ACMETerminologyServiceCapabilityStatement</h2>\n      <div>\n        <p>Example capability statement for a Terminology Server. A server can support more fucntionality than defined here, but this is the minimum amount</p>\n\n      </div>\n      <table>\n        <tr>\n          <td>Mode</td>\n          <td>SERVER</td>\n        </tr>\n        <tr>\n          <td>Description</td>\n          <td/>\n        </tr>\n        <tr>\n          <td>Transaction</td>\n          <td/>\n        </tr>\n        <tr>\n          <td>System History</td>\n          <td/>\n        </tr>\n        <tr>\n          <td>System Search</td>\n          <td/>\n        </tr>\n      </table>\n      <table>\n        <tr>\n          <th>\n            <b>Resource Type</b>\n          </th>\n          <th>\n            <b>Profile</b>\n          </th>\n          <th>\n            <b title=\"GET a resource (read interaction)\">Read</b>\n          </th>\n          <th>\n            <b title=\"GET all set of resources of the type (search interaction)\">Search</b>\n          </th>\n          <th>\n            <b title=\"PUT a new resource version (update interaction)\">Update</b>\n          </th>\n          <th>\n            <b title=\"POST a new resource (create interaction)\">Create</b>\n          </th>\n        </tr>\n        <tr>\n          <td>CodeSystem</td>\n          <td>\n            <a href=\"http://hl7.org/fhir/StructureDefinition/CodeSystem\">http://hl7.org/fhir/StructureDefinition/CodeSystem</a>\n          </td>\n          <td>y</td>\n          <td>y</td>\n          <td/>\n          <td/>\n        </tr>\n        <tr>\n          <td>ValueSet</td>\n          <td>\n            <a href=\"http://hl7.org/fhir/StructureDefinition/ValueSet\">http://hl7.org/fhir/StructureDefinition/ValueSet</a>\n          </td>\n          <td>y</td>\n          <td>y</td>\n          <td/>\n          <td/>\n        </tr>\n        <tr>\n          <td>ConceptMap</td>\n          <td>\n            <a href=\"http://hl7.org/fhir/StructureDefinition/ConceptMap\">http://hl7.org/fhir/StructureDefinition/ConceptMap</a>\n          </td>\n          <td>y</td>\n          <td>y</td>\n          <td/>\n          <td/>\n        </tr>\n      </table>\n    </div>"
                },
                "url" : "http://hl7.org/fhir/CapabilityStatement/terminology-server",
                "version" : "5.0.0",
                "name" : "OCLTerminologyServiceCapabilityStatement",
                "title" : "OCL Terminology Service — Capability Statement",
                "status" : "draft",
                "experimental" : True,
                "date" : "2022-09-01",
                "description" : "Example capability statement for a Terminology Server. A server can support more fucntionality than defined here, but this is the minimum amount",
                "kind" : "instance",
                "implementation" : {
                    "description" : "The OCL FHIR Terminology Server"
                },
                "fhirVersion" : "5.0.0",
                "format" : ["json", "xml"],
                "rest" : [{
                    "mode" : "server",
                    "resource" : [{
                        "type" : "CodeSystem",
                        "profile" : "http://hl7.org/fhir/StructureDefinition/CodeSystem",
                        "interaction" : [{
                            "extension" : [{
                                "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                "valueCode" : "SHALL"
                            }],
                            "code" : "read"
                        },
                            {
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "code" : "search-type"
                            }],
                        "searchParam" : [{
                            "name" : "url",
                            "definition" : "http://hl7.org/fhir/SearchParameter/CodeSystem-url",
                            "type" : "uri"
                        },
                            {
                                "name" : "version",
                                "definition" : "http://hl7.org/fhir/SearchParameter/CodeSystem-version",
                                "type" : "token"
                            },
                            {
                                "name" : "name",
                                "definition" : "http://hl7.org/fhir/SearchParameter/CodeSystem-name",
                                "type" : "string"
                            },
                            {
                                "name" : "title",
                                "definition" : "http://hl7.org/fhir/SearchParameter/CodeSystem-title",
                                "type" : "string"
                            },
                            {
                                "name" : "status",
                                "definition" : "http://hl7.org/fhir/SearchParameter/CodeSystem-status",
                                "type" : "token"
                            }],
                        "operation" : [{
                            "extension" : [{
                                "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                "valueCode" : "SHALL"
                            }],
                            "name" : "expand",
                            "definition" : "http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup"
                        },
                            {
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "name" : "expand",
                                "definition" : "http://hl7.org/fhir/OperationDefinition/CodeSystem-validate-code"
                            },
                            {
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "name" : "expand",
                                "definition" : "http://hl7.org/fhir/OperationDefinition/CodeSystem-subsumes"
                            }]
                    },
                        {
                            "type" : "ValueSet",
                            "profile" : "http://hl7.org/fhir/StructureDefinition/ValueSet",
                            "interaction" : [{
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "code" : "read"
                            },
                                {
                                    "extension" : [{
                                        "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                        "valueCode" : "SHALL"
                                    }],
                                    "code" : "search-type"
                                }],
                            "searchParam" : [{
                                "name" : "url",
                                "definition" : "http://hl7.org/fhir/SearchParameter/ValueSet-url",
                                "type" : "uri"
                            },
                                {
                                    "name" : "version",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ValueSet-version",
                                    "type" : "token"
                                },
                                {
                                    "name" : "name",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ValueSet-name",
                                    "type" : "string"
                                },
                                {
                                    "name" : "title",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ValueSet-title",
                                    "type" : "string"
                                },
                                {
                                    "name" : "status",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ValueSet-status",
                                    "type" : "token"
                                }],
                            "operation" : [{
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "name" : "expand",
                                "definition" : "http://hl7.org/fhir/OperationDefinition/ValueSet-expand"
                            },
                                {
                                    "extension" : [{
                                        "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                        "valueCode" : "SHALL"
                                    }],
                                    "name" : "expand",
                                    "definition" : "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code"
                                }]
                        },
                        {
                            "type" : "ConceptMap",
                            "profile" : "http://hl7.org/fhir/StructureDefinition/ConceptMap",
                            "interaction" : [{
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "code" : "read"
                            },
                                {
                                    "extension" : [{
                                        "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                        "valueCode" : "SHALL"
                                    }],
                                    "code" : "search-type"
                                }],
                            "searchParam" : [{
                                "name" : "url",
                                "definition" : "http://hl7.org/fhir/SearchParameter/ConceptMap-url",
                                "type" : "uri"
                            },
                                {
                                    "name" : "version",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ConceptMap-version",
                                    "type" : "token"
                                },
                                {
                                    "name" : "name",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ConceptMap-name",
                                    "type" : "string"
                                },
                                {
                                    "name" : "title",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ConceptMap-title",
                                    "type" : "string"
                                },
                                {
                                    "name" : "status",
                                    "definition" : "http://hl7.org/fhir/SearchParameter/ConceptMap-status",
                                    "type" : "token"
                                }],
                            "operation" : [{
                                "extension" : [{
                                    "url" : "http://hl7.org/fhir/StructureDefinition/capabilitystatement-expectation",
                                    "valueCode" : "SHALL"
                                }],
                                "name" : "expand",
                                "definition" : "http://hl7.org/fhir/OperationDefinition/ConceptMap-translate"
                            }]
                        }]
                }]
            }
        return Response(response)
