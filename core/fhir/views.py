import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core import settings

logger = logging.getLogger('oclapi')


class CapabilityStatementView(APIView):
    permission_classes = (AllowAny,)

    def get(self, request):
        mode = request.query_params.get('mode')
        fhir_base_url = f"{settings.API_BASE_URL}/fhir"
        if settings.FHIR_SUBDOMAIN:
            #  TODO: it's not always right as 'api.' may not be a part of the API base url
            fhir_base_url = fhir_base_url.replace('://api.', f"://{settings.FHIR_SUBDOMAIN}.", 1)
        if mode == 'terminology':
            response = {
                "resourceType": "TerminologyCapabilities",
                "id": "openconceptlab-terminology-capabilities",
                "text": {
                    "status": "generated",
                    "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">\n\t\t\t\n"
                           "<p>The OCL Server supports the following resources: "
                           "CodeSystem, ValueSet, ConceptMap.</p>\n\t\t\t\n</div>"
                    },
                "url": "urn:uuid:0a651364-aee5-4b4e-8259-39e8a1cff71a",
                "version": "0.1",
                "name": "OCLTerminologyCapabilities",
                "title": "OpenConceptLab FHIR Terminology Capabilities",
                "status": "draft",
                "experimental": True,
                "date": "2024-05-15",
                "publisher": "OpenConceptLab",
                "contact": [{
                    "name": "System Administrator",
                    "telecom": [{
                        "system": "email",
                        "value": "admin@openconceptlab.org"
                    }]
                }],
                "description": "This is the FHIR terminology capability statement for the OCL FHIR.",
                "kind": "instance",
                "software": {
                    "name": "oclfhir",
                    "version": settings.VERSION
                },
                "implementation": {
                    "description": "OCL FHIR Terminology Server",
                    "url": fhir_base_url
                },
                "codeSearch": "all"
            }
        else:
            response = {
                "resourceType": "CapabilityStatement",
                "id": "openconceptlab-terminology-server",
                "text": {
                    "status": "extensions",
                    "div": "<div xmlns=\"http://www.w3.org/1999/xhtml\">\n"
                           "<h2>OpenConceptLab Capability Statement</h2>\n</div>\n"
                },
                "url": "http://hl7.org/fhir/CapabilityStatement/terminology-server",
                "version": "0.1",
                "name": "OCLTerminologyCapabilityStatement",
                "title": "OpenConceptLab FHIR Terminology Server — Capability Statement",
                "status": "draft",
                "experimental": True,
                "date": "2024-05-15",
                "description": "This is a draft capability statement for a OCL FHIR Terminology Server.",
                "kind": "instance",
                "implementation": {
                    "description": "The OCL FHIR Terminology Server"
                },
                "fhirVersion": "4.0.0",
                "format": ["json", "xml"],
                "rest": [
                    {
                        "mode": "server",
                        "resource": [
                            {
                                "type": "CodeSystem",
                                "profile": "http://hl7.org/fhir/StructureDefinition/CodeSystem",
                                "interaction": [
                                    {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "code": "read"
                                    }, {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "code": "search-type"
                                    }
                                ],
                                "searchParam": [
                                    {
                                        "name": "url",
                                        "definition": "http://hl7.org/fhir/SearchParameter/CodeSystem-url",
                                        "type": "uri"
                                    }, {
                                        "name": "version",
                                        "definition": "http://hl7.org/fhir/SearchParameter/CodeSystem-version",
                                        "type": "token"
                                    }, {
                                        "name": "name",
                                        "definition": "http://hl7.org/fhir/SearchParameter/CodeSystem-name",
                                        "type": "string"
                                    }, {
                                        "name": "title",
                                        "definition": "http://hl7.org/fhir/SearchParameter/CodeSystem-title",
                                        "type": "string"},
                                    {
                                        "name": "status",
                                        "definition": "http://hl7.org/fhir/SearchParameter/CodeSystem-status",
                                        "type": "token"
                                    }],
                                "operation": [
                                    {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "name": "expand",
                                        "definition": "http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup"
                                    }, {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "name": "expand",
                                        "definition": "http://hl7.org/fhir/OperationDefinition/"
                                                      "CodeSystem-validate-code"
                                    }
                                    # }, {
                                    #     "extension": [
                                    #         {
                                    #             "url": "http://hl7.org/fhir/StructureDefinition/"
                                    #                    "capabilitystatement-expectation",
                                    #             "valueCode": "SHALL"
                                    #         }
                                    #     ],
                                    #     "name": "expand",
                                    #     "definition": "http://hl7.org/fhir/OperationDefinition/CodeSystem-subsumes"
                                    # }
                                ]
                            }, {
                                "type": "ValueSet",
                                "profile": "http://hl7.org/fhir/StructureDefinition/ValueSet",
                                "interaction": [
                                    {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "code": "read"
                                    }, {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "code": "search-type"
                                    }
                                ],
                                "searchParam": [
                                    {
                                        "name": "url",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ValueSet-url",
                                        "type": "uri"
                                    }, {
                                        "name": "version",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ValueSet-version",
                                        "type": "token"
                                    }, {"name": "name",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ValueSet-name",
                                        "type": "string"
                                        },
                                    {
                                        "name": "title",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ValueSet-title",
                                        "type": "string"
                                    }, {
                                        "name": "status",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ValueSet-status",
                                        "type": "token"
                                    }
                                ],
                                "operation": [
                                    {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "name": "expand",
                                        "definition": "http://hl7.org/fhir/OperationDefinition/ValueSet-expand"
                                    }, {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "name": "expand",
                                        "definition": "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code"
                                    }
                                ]
                            }, {
                                "type": "ConceptMap",
                                "profile": "http://hl7.org/fhir/StructureDefinition/ConceptMap",
                                "interaction": [
                                    {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "code": "read"
                                    }, {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "code": "search-type"
                                    }
                                ],
                                "searchParam": [
                                    {
                                        "name": "url",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ConceptMap-url",
                                        "type": "uri"
                                    }, {
                                        "name": "version",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ConceptMap-version",
                                        "type": "token"
                                    }, {
                                        "name": "name",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ConceptMap-name",
                                        "type": "string"
                                    }, {
                                        "name": "title",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ConceptMap-title",
                                        "type": "string"
                                    }, {
                                        "name": "status",
                                        "definition": "http://hl7.org/fhir/SearchParameter/ConceptMap-status",
                                        "type": "token"
                                    }
                                ],
                                "operation": [
                                    {
                                        "extension": [
                                            {
                                                "url": "http://hl7.org/fhir/StructureDefinition/"
                                                       "capabilitystatement-expectation",
                                                "valueCode": "SHALL"
                                            }
                                        ],
                                        "name": "expand",
                                        "definition": "http://hl7.org/fhir/OperationDefinition/ConceptMap-translate"
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        return Response(response)
