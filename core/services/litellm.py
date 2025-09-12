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

    def __init__(self):
        self.anthropic_api_key = settings.ANTHROPIC_API_KEY

    def recommend(self, map_project, row, candidates, include_default_filter=False):  # pragma: no cover
        prompt = self.get_prompt(map_project, row, candidates, include_default_filter)
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

    def get_prompt(self, map_project, row, candidates, include_default_filter=False):  # pragma: no cover
        project_context = self.get_project_context(
            map_project, include_default_filter=include_default_filter)
        if not project_context:
            raise ValueError("Map project must have a valid target repository.")

        system_prompt = self.RECOMMEND_CANDIDATE_SYSTEM_PROMPT.strip()
        input_prompt = self.RECOMMEND_CANDIDATE_INPUT_PROMPT.strip().format(
            project=json.dumps(project_context, indent=2),
            row=json.dumps(row, indent=2),
            candidates=json.dumps(candidates, indent=2)
        )
        task_prompt = self.RECOMMEND_CANDIDATE_TASK_PROMPT.strip()

        full_prompt = f"{system_prompt}\n\n{input_prompt}\n\n{task_prompt}"
        return full_prompt

    @staticmethod
    def get_project_context(map_project, include_default_filter=False):  # pragma: no cover
        target_repo = map_project.target_repo
        if target_repo:
            return {
              "project": {
                "name": map_project.name,
                "description": map_project.description,
                "domain": "General Medical Terminology"
              },
              "target_repository": {
                "name": target_repo.mnemonic,
                "version": target_repo.version,
                "filters": (target_repo.concept_filter_default if include_default_filter else None) or "Active concepts"
              },
              "matching_config": {
                "algorithms": ["Fuzzy String", "Semantic Vector", "Lexical"],
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
        return completion(model=self.ANTHROPIC_MODEL, messages=[{'content': message, 'role': 'user'}])
