# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from dataclasses import dataclass
import json
from datetime import datetime, timezone
from genlayer.gl.vm import UserError

@allow_storage
@dataclass
class Market:
    id: str
    creator: str
    question: str
    resolution_url_1: str
    resolution_url_2: str
    resolution_url_3: str
    deadline: bigint
    total_yes: bigint
    total_no: bigint
    status: str
    outcome: str
    confidence: bigint
    reason: str

@allow_storage
@dataclass
class Bet:
    user: str
    market_id: str
    side: str
    amount: bigint
    claimed: bool

@allow_storage
class Contract(gl.Contract):
    markets: TreeMap[str, Market]
    bets: DynArray[Bet]
    market_counter: bigint
    treasury_address: str
    owner_address: str

    def __init__(self, initial_treasury: str):
        self.market_counter = bigint(0)
        self.owner_address = str(gl.message.sender_address) if gl.message.sender_address else ""
        self.treasury_address = initial_treasury.strip() if initial_treasury else ""

    def _addr_str(self, addr: Address) -> str:
        try:
            return addr.as_hex
        except Exception:
            return str(addr)

    def _get_treasury_addr(self) -> Address:
        if not self.treasury_address:
            raise UserError("Treasury address not set")
        return Address(self.treasury_address)

    def _parse_llm_json(self, text) -> dict:
        if isinstance(text, dict):
            return text
        try:
            cleaned = str(text).strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            elif cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            return json.loads(cleaned.strip())
        except Exception as e:
            return {"outcome": "INVALID", "confidence": 0, "reason": f"Parse error: {str(e)}"}

    @gl.public.write
    def set_treasury(self, addr: str) -> None:
        if self._addr_str(gl.message.sender_address) != self.owner_address:
            raise UserError("Only owner can set treasury")
        addr = addr.strip()
        if not addr:
            raise UserError("Treasury address required")
        _ = Address(addr)
        self.treasury_address = addr

    @gl.public.write
    def create_market(
        self,
        question: str,
        resolution_url_1: str,
        resolution_url_2: str,
        resolution_url_3: str,
        deadline: bigint,
    ) -> str:
        question = question.strip()
        resolution_url_1 = resolution_url_1.strip()
        resolution_url_2 = resolution_url_2.strip() if resolution_url_2 else ""
        resolution_url_3 = resolution_url_3.strip() if resolution_url_3 else ""

        if len(question) < 10:
            raise UserError("Question too short (min 10 chars)")

        if not resolution_url_1:
            raise UserError("At least resolution_url_1 is required")

        for url, label in (
            (resolution_url_1, "resolution_url_1"),
            (resolution_url_2, "resolution_url_2"),
            (resolution_url_3, "resolution_url_3"),
        ):
            if url and not (url.startswith("https://") or url.startswith("http://")):
                raise UserError(label + " must start with http:// or https://")

        self.market_counter += bigint(1)
        mid = str(self.market_counter)

        self.markets[mid] = Market(
            id=mid,
            creator=self._addr_str(gl.message.sender_address),
            question=question,
            resolution_url_1=resolution_url_1,
            resolution_url_2=resolution_url_2,
            resolution_url_3=resolution_url_3,
            deadline=deadline,
            total_yes=bigint(0),
            total_no=bigint(0),
            status="OPEN",
            outcome="",
            confidence=bigint(0),
            reason="",
        )
        return mid

    @gl.public.write.payable
    def place_bet(self, market_id: str, side: str) -> None:
        amount = gl.message.value
        if amount <= bigint(0):
            raise UserError("Bet amount must be greater than 0")
        if side not in ("YES", "NO"):
            raise UserError("Side must be YES or NO")

        if market_id not in self.markets:
            raise UserError("Market not found")

        m = self.markets[market_id]
        
        # Enforce market deadline strictly and securely via node-context time
        current_timestamp = bigint(int(datetime.now(timezone.utc).timestamp()))
        if current_timestamp >= m.deadline:
            raise UserError("Market has expired")
        if m.status != "OPEN":
            raise UserError("Market is not open for betting")

        self.bets.append(
            Bet(
                user=self._addr_str(gl.message.sender_address),
                market_id=market_id,
                side=side,
                amount=amount,
                claimed=False,
            )
        )

        if side == "YES":
            m.total_yes += amount
        else:
            m.total_no += amount

        self.markets[market_id] = m

    @gl.public.write
    def cancel_market(self, market_id: str) -> None:
        if market_id not in self.markets:
            raise UserError("Market not found")

        m = self.markets[market_id]
        if self._addr_str(gl.message.sender_address) != m.creator:
            raise UserError("Only creator can cancel")
        if m.status != "OPEN":
            raise UserError("Market is not open")
        if m.total_yes > bigint(0) or m.total_no > bigint(0):
            raise UserError("Cannot cancel: bets already placed")

        m.status = "CANCELLED"
        self.markets[market_id] = m

    @gl.public.write
    def resolve(self, market_id: str) -> None:
        if market_id not in self.markets:
            raise UserError("Market not found")

        m = self.markets[market_id]
        
        # Strict resolution constraint: Cannot resolve before deadline
        current_timestamp = bigint(int(datetime.now(timezone.utc).timestamp()))
        if current_timestamp < m.deadline:
            raise UserError("Market deadline has not passed yet")
        if m.status != "OPEN":
            raise UserError("Market is not open")

        q_str = str(m.question)
        u1_str = str(m.resolution_url_1)
        u2_str = str(m.resolution_url_2)
        u3_str = str(m.resolution_url_3)

        def leader_fn():
            sources = []
            successful_fetches = 0
            for u in (u1_str, u2_str, u3_str):
                if u:
                    try:
                        res = gl.nondet.web.render(u, mode="text")
                        text = res.content if hasattr(res, "content") else str(res)
                        if text and len(text.strip()) >= 20:
                            sources.append(f"Source ({u}):\n{text[:3000]}")
                            successful_fetches += 1
                    except Exception:
                        pass

            if successful_fetches == 0:
                return {"outcome": "INVALID", "confidence": 0, "reason": "all_sources_failed"}

            combined = "\n\n".join(sources)
            prompt = f"""
SYSTEM: You are a strict resolution engine for a prediction market.
QUESTION: {q_str}

RESOLUTION SOURCES:
{combined}

Rules:
- If sources consistently support YES -> "YES"
- If sources consistently support NO -> "NO"
- If sources conflict, are insufficient, or unreadable -> "INVALID"

OUTPUT ONLY JSON:
{{"outcome": "YES|NO|INVALID", "confidence": 0-100, "reason": "max 300 chars"}}
"""
            try:
                raw = gl.nondet.exec_prompt(prompt, response_format="json")
                if isinstance(raw, dict):
                    parsed = raw
                else:
                    text = raw.content if hasattr(raw, "content") else str(raw)
                    parsed = self._parse_llm_json(text)

                # Bind confidence directly to outcome to prevent validator discrepancies
                conf = int(parsed.get("confidence", 0))
                outcome = str(parsed.get("outcome", "INVALID")).upper()
                reason = str(parsed.get("reason", ""))

                if conf < 60 and outcome != "INVALID":
                    outcome = "INVALID"
                    reason = f"[low_confidence: {conf}%] " + reason

                return {
                    "outcome": outcome,
                    "confidence": conf,
                    "reason": reason[:300],
                }
            except Exception as e:
                return {"outcome": "INVALID", "confidence": 0, "reason": f"LLM error: {str(e)}"}

        def validator_fn(leader_res) -> bool:
            if not isinstance(leader_res, gl.vm.Return):
                return False
            leader_data = leader_res.calldata if hasattr(leader_res, "calldata") else leader_res
            if not isinstance(leader_data, dict):
                leader_data = self._parse_llm_json(str(leader_data))

            mine_data = leader_fn()
            
            # Semantic agreement on verified outcome
            return str(leader_data.get("outcome", "")).upper() == str(mine_data.get("outcome", "")).upper()

        result = gl.vm.run_nondet(leader_fn, validator_fn)
        if not isinstance(result, dict):
            result = self._parse_llm_json(str(result))

        outcome = str(result.get("outcome", "INVALID")).upper()
        if outcome not in ("YES", "NO", "INVALID"):
            outcome = "INVALID"

        confidence = bigint(int(result.get("confidence", 0)))
        reason = str(result.get("reason", "Resolved by AI consensus"))

        m.outcome = outcome
        m.confidence = confidence
        m.reason = reason
        m.status = "RESOLVED"
        self.markets[market_id] = m

        total_yes = m.total_yes
        total_no = m.total_no
        total_pool = total_yes + total_no

        # Handle full refunds for INVALID outcomes or empty pools
        if outcome == "INVALID" or total_pool == bigint(0):
            for i in range(len(self.bets)):
                b = self.bets[i]
                if b.market_id == market_id and not b.claimed:
                    if b.amount > bigint(0):
                        gl.get_contract_at(Address(b.user)).emit_transfer(value=b.amount)
                    b.claimed = True
                    self.bets[i] = b
            return

        # 2% protocol fee
        protocol_fee = (total_pool * bigint(2)) // bigint(100)
        remaining = total_pool - protocol_fee

        if protocol_fee > bigint(0):
            gl.get_contract_at(self._get_treasury_addr()).emit_transfer(value=protocol_fee)

        winning_total = total_yes if outcome == "YES" else total_no

        if winning_total == bigint(0):
            if remaining > bigint(0):
                gl.get_contract_at(self._get_treasury_addr()).emit_transfer(value=remaining)
            for i in range(len(self.bets)):
                b = self.bets[i]
                if b.market_id == market_id:
                    b.claimed = True
                    self.bets[i] = b
            return

        # Distribute proportional payouts to winners
        for i in range(len(self.bets)):
            b = self.bets[i]
            if (
                b.market_id == market_id
                and b.side == outcome
                and not b.claimed
                and b.amount > bigint(0)
            ):
                share = (b.amount * remaining) // winning_total
                if share > bigint(0):
                    gl.get_contract_at(Address(b.user)).emit_transfer(value=share)
                b.claimed = True
                self.bets[i] = b

    @gl.public.view
    def get_market(self, market_id: str) -> str:
        if market_id not in self.markets:
            raise UserError("Market not found")
        m = self.markets[market_id]
        return json.dumps({
            "id": m.id,
            "creator": m.creator,
            "question": m.question,
            "resolution_url_1": m.resolution_url_1,
            "resolution_url_2": m.resolution_url_2,
            "resolution_url_3": m.resolution_url_3,
            "deadline": str(m.deadline),
            "total_yes": str(m.total_yes),
            "total_no": str(m.total_no),
            "status": m.status,
            "outcome": m.outcome,
            "confidence": str(m.confidence),
            "reason": m.reason,
        })
