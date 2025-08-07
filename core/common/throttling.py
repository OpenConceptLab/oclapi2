from django.conf import settings
from pydash import get
from rest_framework.throttling import UserRateThrottle


class GuestMinuteThrottle(UserRateThrottle):
    scope = 'guest_minute'


class GuestDayThrottle(UserRateThrottle):
    scope = 'guest_day'


class StandardMinuteThrottle(UserRateThrottle):
    scope = 'standard_minute'


class StandardDayThrottle(UserRateThrottle):
    scope = 'standard_day'


class ThrottleUtil:
    @staticmethod
    def get_limit_remaining(throttle, request, view):
        key = throttle.get_cache_key(request, view)
        if key is not None:
            history = throttle.cache.get(key, None)
            if history is None:
                return None
            while history and history[-1] <= throttle.timer() - throttle.duration:
                history.pop()
            remaining = throttle.num_requests - len(history)
        else:
            remaining = 'unlimited'

        return remaining

    @staticmethod
    def get_throttles_by_user_plan(user):
        if not settings.ENABLE_THROTTLING:
            return []
        # order is important, first one has to be minute throttle
        if get(user, 'api_rate_limit.is_guest') or not get(user, 'is_authenticated'):
            return [GuestMinuteThrottle(), GuestDayThrottle()]
        return [StandardMinuteThrottle(), StandardDayThrottle()]
