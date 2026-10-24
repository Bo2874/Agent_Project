"""Deterministic project DecisionProvider fixture; no Jev SDK/HTTP compatibility mock."""

from agent_core.routing.types import DecisionResult


class ShortlistPolicyFixture:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    async def decide(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        option = next(o for o in request.options if o.id == answer or answer in o.description)
        return DecisionResult(
            selected_id=option.id,
            probabilities={o.id: float(o.id == option.id) for o in request.options},
        )
