MODE_OFF = 'off'  # no counting, no headers
MODE_SHADOW = 'shadow'  # count, log and send headers, but never refuse
MODE_ENFORCE = 'enforce'  # refuse with 429 + Retry-After when a lane is full
MODES = (MODE_OFF, MODE_SHADOW, MODE_ENFORCE)

# Which clients enforce mode refuses. The rest stay in shadow: counted and logged, never refused.
ENFORCE_FOR_AWARE = 'aware'  # only clients whose X-OCL-Event-Metadata carries "capacity_aware": "true"
ENFORCE_FOR_ALL = 'all'
ENFORCE_FOR = (ENFORCE_FOR_AWARE, ENFORCE_FOR_ALL)
CAPACITY_AWARE_METADATA_KEY = 'capacity_aware'

# A user's tier is their highest plan group, highest first.
TIER_STAFF = 'staff'
TIER_CORE = 'core'
TIER_EARLY_ACCESS = 'early_access'
TIER_PREVIEW = 'preview'
TIERS = (TIER_STAFF, TIER_CORE, TIER_EARLY_ACCESS, TIER_PREVIEW)

# Lanes: each counts the heavy calls in flight in one scope.
LANE_API_HEAVY = 'api_heavy'  # every heavy call, cluster-wide
LANE_API_HEAVY_TASK = 'api_heavy_task'  # every heavy call on this API task
LANE_ES2_KNN = 'es2_knn'  # semantic $match calls, which run kNN searches in Elasticsearch
LANE_TIER = 'tier'  # the calls of every user in this tier
LANE_USER = 'user'  # this user's calls
LANES = (LANE_API_HEAVY, LANE_API_HEAVY_TASK, LANE_ES2_KNN, LANE_TIER, LANE_USER)

DECISION_ADMITTED = 'admitted'
DECISION_SHADOW_REFUSED = 'shadow-refused'  # a lane was full; enforce mode would have refused it
DECISION_REFUSED = 'refused'
DECISION_UNAVAILABLE = 'unavailable'  # Redis couldn't be reached, so the call went ahead uncounted

ENDPOINT_MATCH = '$match'
ENDPOINT_RERANK = '$rerank'

HEADER_DECISION = 'X-OCL-Capacity-Decision'
HEADER_LIMIT = 'X-OCL-Capacity-Limit'
HEADER_IN_FLIGHT = 'X-OCL-Capacity-In-Flight'
HEADER_TIER = 'X-OCL-Capacity-Tier'
HEADER_TIER_LIMIT = 'X-OCL-Capacity-Tier-Limit'
HEADER_TIER_IN_FLIGHT = 'X-OCL-Capacity-Tier-In-Flight'
HEADER_SUGGESTED_CONCURRENCY = 'X-OCL-Capacity-Suggested-Concurrency'
HEADERS = (
    HEADER_DECISION, HEADER_LIMIT, HEADER_IN_FLIGHT, HEADER_TIER, HEADER_TIER_LIMIT, HEADER_TIER_IN_FLIGHT,
    HEADER_SUGGESTED_CONCURRENCY,
)

CAPACITY_EXCEEDED_ERROR_CODE = 'capacity_exceeded'

# The "event" of the JSON log lines, which CloudWatch metric filters match on.
LOG_EVENT = 'ocl_capacity'
CONFIG_LOG_EVENT = 'ocl_capacity_config'

REDIS_KEY_PREFIX = 'ocl:capacity'

SOURCE_API = 'api'
SOURCE_COMMAND = 'command'
