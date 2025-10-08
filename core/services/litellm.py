# pylint: disable=line-too-long
import json

from django.conf import settings
from litellm import completion
from pydash import get


class LiteLLMService:
    ANTHROPIC_MODEL = "anthropic/claude-sonnet-4-20250514"

    RECOMMEND_CANDIDATE_SYSTEM_PROMPT = """
    You are an expert medical terminology curator evaluating candidate matches for standardizing local clinical terms to international medical terminologies. 
    Your role is to assess candidate concepts returned by matching algorithms and provide structured recommendations that prioritize clinical accuracy, semantic precision, and implementation safety.
    
    ### Core Objectives
    - **Clinical Safety**: Ensure matches preserve critical clinical meaning and prevent misinterpretation
    - **Semantic Precision**: Select candidates that best capture the intended clinical concept
    - **Implementation Viability**: Consider practical constraints like data types, hierarchies, and system compatibility
    - **Quality Assurance**: Flag ambiguous or potentially problematic matches for human review
    
    ### Methodology
    1. Analyze the input term's clinical context, concept class, and intended use
    2. Evaluate each candidate's semantic alignment, specificity, and clinical appropriateness
    3. Consider algorithm confidence scores alongside clinical judgment
    4. Apply project-specific rules and constraints
    5. Provide structured recommendations with clear rationale
    
    ### Decision Framework
    
    **RECOMMEND**: Single candidate with high semantic alignment (>85% confidence or clear clinical match)
    **CONDITIONAL**: Good candidate(s) exist but with specific limitations or requirements  
    **REJECT**: No candidates meet minimum quality thresholds
    **INSUFFICIENT**: Cannot make confident assessment with available information
    
    You must respond with a valid JSON object following the specified output template.
    """
    RECOMMEND_CANDIDATE_SYSTEM_PROMPT_V2 = """
    You are a medical terminology curator evaluating candidate concept matches for standardizing local clinical terms to target terminologies (LOINC, SNOMED CT, or custom). Assess algorithm-generated candidates and provide structured recommendations prioritizing semantic precision, clinical safety, and implementation viability.

    Core Methodology:
    1. Analyze input term: name, description, properties, clinical context, intended use
    2. Evaluate each candidate: semantic alignment, specificity, clinical appropriateness  
    3. Consider algorithm confidence and methodology alongside clinical judgment
    4. Apply project-specific rules and constraints
    5. Provide structured recommendations with clear rationale
    
    Recommendation Types:
    - RECOMMEND: Single candidate with strong alignment (>90% confidence or clear clinical match)
    - CONDITIONAL: Good candidate(s) exist with specific limitations or require additional context
    - REJECT: No candidates meet minimum thresholds
    - INSUFFICIENT: Cannot assess confidently with available information
    
    CRITICAL Constraints:
    - Primary and alternative candidates MUST come from the provided candidate pool only. The map targets for CIEL bridge terminology candidates are considered part of the candidate pool.
    - Out-of-scope suggestions (use sparingly) capture concepts NOT in the candidate list that may be relevant, but MUST be in the same target repository
    - Prioritize primary_mapped_fields as the primary basis for evaluating match candidates. Use additional_metadata fields as secondary signals, but they should not override strong mismatches in primary fields. Can ignore fields like id, pk, serial number, etc.
    - Assess alignment and suitability; avoid unverifiable claims about "clinical value" or "safety"
    - Note data gaps explicitly rather than making unsupported determinations
    - Be transparent about uncertainty and missing information
    - If a candidate’s rank within the target repo (not within the match results) is available, preference for higher ranking candidates, though alignment and accuracy is much more important
    
    Before finalizing response:
    1. Verify primary_candidate.concept_id is from input list or null
    2. Verify all alternative_candidates concept_ids are from input list  
    3. Place any other concepts in out_of_scope_suggestions
    
    Output valid JSON only.
    """

    RECOMMEND_CANDIDATE_INPUT_PROMPT = """
    Evaluate the following medical terminology matching task:

    ## Project Context
    {project}
    
    ## Input Row
    {row}
    
    ## Candidate Pool
    {candidates}
    """

    RECOMMEND_CANDIDATE_TASK_PROMPT = """
    ## Task
    Please evaluate these candidates and provide your recommendation following the structured output template. Focus on:
    1. Semantic alignment between the input term and candidates
    2. Clinical safety and appropriateness
    3. Implementation viability
    
    Respond with a JSON object following this structure:
    {
      "recommendation": "RECOMMEND|CONDITIONAL|REJECT|INSUFFICIENT",
      "primary_candidate": {
        "concept_id": "[Selected concept ID or null]",
        "confidence_level": "HIGH|MEDIUM|LOW",
        "match_strength": "[Semantic alignment percentage]"
      },
      "alternative_candidates": [
        {
          "concept_id": "[Alternative concept ID]",
          "rank": "[Ranking order]",
          "rationale": "[Why this is an alternative]"
        }
      ],
      "conditions_and_caveats": [
        "[Any specific conditions for CONDITIONAL recommendations]"
      ],
      "rationale": {
        "structured": {
          "semantic_alignment": "[Assessment of meaning preservation]",
          "specificity_level": "[Too broad/appropriate/too narrow]",
          "clinical_safety": "[Risk assessment]",
          "algorithm_consensus": "[Agreement across algorithms]",
          "implementation_complexity": "[Easy/Medium/Complex]",
          "data_compatibility": "[Compatible/Requires mapping/Incompatible]"
        },
        "narrative": "[2-3 sentence explanation of the recommendation and key factors]"
      },
      "quality_flags": [
        "[Any concerns or notable observations]"
      ],
      "additional_information_needed": [
        "[For INSUFFICIENT recommendations, specify what's needed]"
      ]
    }
    
    Important: Your response must be a valid JSON object only, with no additional text or explanation outside the JSON.
    """
    RECOMMEND_CANDIDATE_TASK_PROMPT_V2 = """
    Evaluate candidates and provide recommendation as JSON following this exact schema:
    {
      "recommendation": "RECOMMEND|CONDITIONAL|REJECT|INSUFFICIENT",
      "primary_candidate": {
        "concept_id": "string or null",
        "confidence_level": "HIGH|MEDIUM|LOW", 
        "match_strength": "percentage as string"
      },
      "alternative_candidates": [
        {
          "concept_id": "string from candidate list",
          "rank": "integer",
          "rationale": "why this is alternative"
        }
      ],
      "out_of_scope_suggestions": [
        {
          "suggested_concept": "specific concept ID, family of codes, or area for exploration within target repository",
          "rationale": "why this would improve the match or should be verified",
          "source": "clinical_knowledge|algorithm_gap|terminology_limitation|partial_match_extension"
        }
      ],
      "rationale": "Concisely explain reasoning for recommendation and provide actionable guidance for human reviewer addressing: (1) Alignment of specific data points that contribute to a confident mapping - do not indicate that a user should proceed with a selection, simply say why they might choose a candidate. (2) What specifically should I verify before accepting? Are there specific data points that, if available, would help to make a full determination? (3) Should I refine the search? If so, what terms, filters, or concept areas should I explore? (4) What is the key decision point or uncertainty I need to resolve? Focus on making the human's mapping decision and any follow-up searching faster and more targeted."
    }
    
    Selection Rules:
    1. Primary/alternative candidates MUST be from input candidate list
    2. Assess semantic alignment between input term and candidates
    3. Apply project-specific rules and constraints
    4. Flag data gaps preventing confident determination
    
    Out-of-Scope Suggestions (use sparingly):
    - MUST be from the same target repository as candidates. If a custom target repository, then it is probably not possible to provide out-of-scope suggestions.
    - Only provide when: (a) primary/alternative candidates are weak/absent, OR (b) verification against specific other concepts is highly recommended
    - Format as: specific concept ID (e.g., "LOINC 12345-6"), family of codes (e.g., "LOINC 2345-* series for method-specific variants"), or exploration suggestion (e.g., "Search for more specific timing qualifiers")
    - Do NOT provide if primary and alternative candidates are strong unless verification is critical
    
    Return only valid JSON with no additional text before or after.
    
    Example for RECOMMEND:
    {
      "recommendation": "RECOMMEND",
      "primary_candidate": {
        "concept_id": "58450-8",
        "confidence_level": "HIGH",
        "match_strength": "95%"
      },
      "alternative_candidates": [],
      "out_of_scope_suggestions": [],
      "rationale": "58450-8 has strong alignment on system (Urine), property (PrThr), and scale (Ord) with high algorithm consensus (>90% across 3 algorithms). Verify that the local term's qualitative result categories align with standard ordinal values (Negative/Trace/1+/2+/3+)."
    }
    
    Example for CONDITIONAL:
    {
      "recommendation": "CONDITIONAL",
      "primary_candidate": {
        "concept_id": "2345-7",
        "confidence_level": "MEDIUM",
        "match_strength": "78%"
      },
      "alternative_candidates": [
        {
          "concept_id": "2345-8",
          "rank": 2,
          "rationale": "Alternative method specification; select if local term uses confirmatory testing"
        }
      ],
      "out_of_scope_suggestions": [
        {
          "suggested_concept": "LOINC 2345-* family with method qualifiers",
          "rationale": "If method specificity is critical for your use case, verify which method your lab uses",
          "source": "terminology_limitation"
        }
      ],
      "rationale": "Cannot select between 2345-7 (general) and 2345-8 (confirmatory method) without knowing your lab's testing methodology. Both are semantically appropriate. Decision point: Does your local term imply a specific testing method? If method doesn't matter for your use case, proceed with 2345-7. If method IS critical, verify lab protocol then choose accordingly. Consider searching 'glucose METHOD confirmatory' to see full method-specific options."
    }
    
    Example for INSUFFICIENT:
    {
      "recommendation": "INSUFFICIENT",
      "primary_candidate": null,
      "alternative_candidates": [],
      "out_of_scope_suggestions": [
        {
          "suggested_concept": "Explore LOINC codes with System='Serum' Property='MCnc'",
          "rationale": "Current candidates have wrong specimen type; need to refine search",
          "source": "algorithm_gap"
        }
      ],
      "rationale": "Cannot recommend from current candidates - all have System='Urine' but your local term specifies serum testing. Refine search with: (1) Add filter 'System=Serum', (2) Search for 'glucose serum mass concentration', (3) Review LOINC codes in 2345-2349 range which covers serum glucose variants. Key gap: Need candidates matching the correct specimen type."
    }
    """

    mock_response = {
        'id': 'chatcmpl-4531c7dd-e464-4d8b-a441-ecb2a4ce3940',
        'created': 1757647505,
        'model': 'claude-sonnet-4-20250514',
        'object': 'chat.completion',
        'system_fingerprint': None,
        'choices': [{
                        'finish_reason': 'stop',
                        'index': 0,
                        'message': {
                            'content': {
                                'recommendation': 'RECOMMEND',
                                'primary_candidate': {
                                    'concept_id': '1305',
                                    'confidence_level': 'HIGH',
                                    'match_strength': '100%'
                                },
                                'alternative_candidates': [{
                                                               'concept_id': '856',
                                                               'rank': 2,
                                                               'rationale': 'Generic HIV viral load test without qualitative specification - less precise but related concept'
                                                           }],
                                'conditions_and_caveats': [],
                                'rationale': {
                                    'structured': {
                                        'semantic_alignment': 'Perfect match - identical terminology with exact preservation of clinical meaning',
                                        'specificity_level': 'Appropriate - maintains the qualitative nature distinction from quantitative viral load tests',
                                        'clinical_safety': 'Excellent - no risk of misinterpretation, preserves critical distinction between qualitative and quantitative testing',
                                        'algorithm_consensus': 'Strong consensus with normalized score of 100.0 and exact term highlighting',
                                        'implementation_complexity': 'Easy - direct mapping with no transformation required',
                                        'data_compatibility': 'Fully compatible - both are Test class concepts with Coded datatype expectations'
                                    },
                                    'narrative': 'The primary candidate (1305) provides an exact lexical match with perfect semantic alignment, preserving the critical clinical distinction between qualitative and quantitative HIV viral load testing. This match poses no clinical safety risks and requires no implementation complexity, making it the optimal choice for standardization.'
                                },
                                'quality_flags': [
                                    'Exceptionally high confidence match with 100% normalized search score',
                                    'Exact term match eliminates ambiguity'],
                                'additional_information_needed': []
                            },
                            'role': 'assistant',
                            'tool_calls': None,
                            'function_call': None
                        }
                    }],
        'usage': {
            'completion_tokens': 408,
            'prompt_tokens': 3781,
            'total_tokens': 4189,
            'completion_tokens_details': None,
            'prompt_tokens_details': {
                'audio_tokens': None,
                'cached_tokens': 0,
                'text_tokens': None,
                'image_tokens': None
            },
            'cache_creation_input_tokens': 0,
            'cache_read_input_tokens': 0
        }
    }

    mock_response_2 = {
        "id": "chatcmpl-c90e8265-8de9-4b5c-82a1-708f429df24f",
        "created": 1759831049,
        "model": "claude-sonnet-4-20250514",
        "object": "chat.completion",
        "system_fingerprint": None,
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": {
                        "recommendation": "CONDITIONAL",
                        "primary_candidate": {
                            "concept_id": "14635-7",
                            "confidence_level": "MEDIUM",
                            "match_strength": "75%"
                        },
                        "alternative_candidates": [
                            {
                                "concept_id": "1989-3",
                                "rank": 2,
                                "rationale": "Alternative mapping from CIEL bridge; may have different method or specificity requirements"
                            },
                            {
                                "concept_id": "55814-8",
                                "rank": 3,
                                "rationale": "Third option from CIEL bridge mapping; verify if this variant better matches your local implementation"
                            }
                        ],
                        "out_of_scope_suggestions": [
                            {
                                "suggested_concept": "Search LOINC for '25-hydroxyvitamin D' with System='Serum' Property='MCnc'",
                                "rationale": "Current candidates derived through CIEL bridge may not be optimal direct matches; search for exact 25-hydroxyvitamin D tests",
                                "source": "algorithm_gap"
                            }
                        ],
                        "rationale": "The primary candidate 14635-7 comes from CIEL concept 168183 ('25-hydroxyvitamin D3 measurement') which has strong semantic alignment with your input term '25 Hydroxy Vitamin D' and synonyms '25OHVITD'. However, all candidates are derived through CIEL bridge mappings rather than direct LOINC matches, and the ocl-semantic results show general vitamin D concepts but not specific 25-hydroxyvitamin D tests. Key verification needed: (1) Confirm the LOINC codes 14635-7, 1989-3, and 55814-8 have correct specimen type (serum) and measure 25-hydroxyvitamin D specifically, (2) Check if method specifications matter for your use case. Consider refining search with exact term '25-hydroxyvitamin D serum' to find direct LOINC matches rather than relying solely on CIEL bridge mappings."
                    },
                    "role": "assistant",
                    "tool_calls": None,
                    "function_call": None
                }
            }
        ],
        "usage": {
            "completion_tokens": 505,
            "prompt_tokens": 18184,
            "total_tokens": 18689,
            "completion_tokens_details": None,
            "prompt_tokens_details": {
                "audio_tokens": None,
                "cached_tokens": 0,
                "text_tokens": None,
                "image_tokens": None
            },
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0
        }
    }

    def __init__(self):
        self.anthropic_api_key = settings.ANTHROPIC_API_KEY

    def recommend(self, map_project, row, metadata, candidates, bridge_candidates=None, scispacy_candidates=None, include_default_filter=False):  # pragma: no cover  # pylint: disable=too-many-arguments,line-too-long
        prompt = self.get_prompt(
            map_project, row, metadata, candidates, bridge_candidates, scispacy_candidates, include_default_filter)
        print("*****LLM as Judge Prompt: Start****")
        print(prompt)
        print("*****LLM as Judge Prompt: END****")
        response = self.__call_anthropic(prompt)
        print("****ANT RESPONSE****")
        return response

    @staticmethod
    def to_dict(response):  # pragma: no cover
        data = response.to_dict()
        try:
            content = get(data, 'choices.0.message.content', '').replace('```json', '').replace('```', '')
            parsed_content = json.loads(content)
            if parsed_content:
                data['choices'][0]['message']['content'] = parsed_content
        except (json.JSONDecodeError, TypeError):
            pass
        return data

    @staticmethod
    def clean_candidate(candidate, locales=None):  # pragma: no cover
        candidate.pop('checksums', None)
        candidate.pop('uuid', None)
        candidate.pop('created_at', None)
        candidate.pop('created_on', None)
        candidate.pop('created_by', None)
        candidate.pop('updated_at', None)
        candidate.pop('updated_on', None)
        candidate.pop('updated_by', None)
        candidate.pop('versions_url', None)
        candidate.pop('public_can_view', None)
        candidate.pop('versioned_object_id', None)
        candidate.pop('latest_source_version', None)
        candidate.pop('update_comment', None)
        candidate.pop('version', None)
        if not candidate.get('external_id', None):
            candidate.pop('external_id', None)

        locales_ = locales.split(',') if locales else []
        names = []
        for name in candidate.get('names', []):
            if not locales or name.get('locale', None) in locales_:
                name_ = {k: v for k, v in name.items() if
                         k in ['name', 'locale', 'external_id', 'name_type', 'locale_preferred'] and v}
                names.append(name_)
        candidate['names'] = names

        properties = candidate.get('properties', [])
        existing_extras = candidate.get('extras', {})
        if existing_extras and properties and isinstance(properties, list):
            extras = {}
            for k, v in existing_extras.items():
                if not next((prop for prop in properties if prop.get('code') == k), None):
                    extras[k] = v
            candidate['extras'] = extras
        mappings = []
        for mapping in (get(candidate, 'mappings', []) or []):
            mapping_ = {k: v for k, v in mapping.items() if
                        k not in ['checksums', 'id', 'sort_weight', 'version_url', 'extras', 'to_concept_code',
                                  'to_concept_url'] and v}
            mappings.append(mapping_)
        candidate['mappings'] = mappings

        return candidate

    def get_prompt(self, map_project, row, metadata, candidates, bridge_candidates=None, scispacy_candidates=None, include_default_filter=False):  # pragma: no cover  # pylint: disable=too-many-arguments,line-too-long,too-many-locals
        project_context = self.get_project_context(
            map_project, include_default_filter=include_default_filter)
        if not project_context:
            raise ValueError("Map project must have a valid target repository.")

        system_prompt = self.RECOMMEND_CANDIDATE_SYSTEM_PROMPT_V2.strip()
        locales = get(map_project, 'filters.locale') or None
        all_candidates = {
            'ocl-semantic': [self.clean_candidate(candidate, locales) for candidate in (candidates or [])],
        }
        if bridge_candidates:
            all_candidates['ciel-bridge'] = [
                self.clean_candidate(candidate, locales) for candidate in bridge_candidates]
        if scispacy_candidates:
            all_candidates['scispacy-loinc'] = [
                self.clean_candidate(candidate, locales) for candidate in scispacy_candidates]
        full_row = {'primary_mapped_fields': row}
        if metadata:
            full_row['additional_metadata'] = metadata
        input_prompt = self.RECOMMEND_CANDIDATE_INPUT_PROMPT.strip().format(
            project=json.dumps(project_context, indent=2),
            row=json.dumps(full_row, indent=2),
            candidates=json.dumps(all_candidates, indent=2)
        )
        task_prompt = self.RECOMMEND_CANDIDATE_TASK_PROMPT_V2.strip()

        full_prompt = f"{system_prompt}\n\n{input_prompt}\n\n{task_prompt}"
        return full_prompt

    @staticmethod
    def get_project_context(map_project, include_default_filter=False):  # pragma: no cover
        target_repo = map_project.target_repo
        project_filters = {
            **(map_project.filters or {}),
            **(target_repo.concept_filter_default if include_default_filter else {})
        }
        if target_repo:
            return {
              "project": {
                "name": map_project.name,
                "description": map_project.description,
              },
              "target_repository": {
                "name": target_repo.mnemonic,
                "version": target_repo.version,
                "description": target_repo.description,
                "filters": project_filters or "Active concepts"
              },
              "matching_config": {
                "algorithms": {
                    "ocl-semantic": "Cosine similarity search on names combined with string matching on properties. Default embedding uses miniLM for cross-lingual support, but multiple models are supported. Normalized score is percentile where the best match is always 100%. Raw score is BM25 returned directly by Elastic Search hybrid search query.",
                    "ciel-bridge": "Lexical and semantic search on the names for the Columbia International eHealth Laboratory (CIEL) interface terminology. Applicable when CIEL has maps to the project\u2019s target repository. This can significantly expand lexical variance, making it more likely to match the source term. Candidates returned by this algorithm are CIEL concepts with 1 or more maps to the target terminology. However, the top-candidate recommended by this prompt must be only one of the maps to the target terminology, and the rationale MUST indicate that it was derived through a CIEL concept. Normalized score is percentile where the best match is always 100%. Raw score is BM25 returned directly by Elastic Search hybrid search query.",
                    "scispacy-loinc": "UMLS entity matching to LOINC parts with novel reassembly of LOINC Parts into LOINC candidates. Only matches the name. Score is 0..1 to simulate a cosine similarity score.",
                },
                "fields_mapped": map_project.fields_mapped,
                "thresholds": map_project.score_configuration or {}
              },
              "quality_requirements": {
                "minimum_confidence": "70%",
                "require_exact_class_match": False,
                "prefer_active_concepts": True
              }
            }
        return False


    def __call_anthropic(self, message):  # pragma: no cover
        return completion(model=self.ANTHROPIC_MODEL, messages=[{'content': message, 'role': 'user'}], temperature=0.2)
