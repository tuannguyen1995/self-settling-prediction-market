import pytest
import json
from datetime import datetime, timezone
from gltest.exceptions import DeploymentError

def assert_user_error(excinfo, expected_msg):
    assert excinfo.value.__class__.__name__ == "UserError"
    assert expected_msg in str(excinfo.value)

@pytest.fixture
def setup_contract(direct_deploy):
    owner = "0xDB7feDfd621F271B099586cF02440dDd9b4061AB"
    contract = direct_deploy("contracts/prediction_market.py", owner)
    return contract, owner

def test_deploy_and_initial_state(setup_contract):
    contract, owner = setup_contract
    assert contract is not None

def test_create_market(setup_contract, direct_vm):
    contract, _ = setup_contract
    
    base_time_str = "2026-08-15T00:00:00Z"
    direct_vm.warp(base_time_str)
    base_timestamp = int(datetime.fromisoformat(base_time_str.replace("Z", "+00:00")).timestamp())
    deadline = base_timestamp + 3600
    
    mid = contract.create_market("Will the price of ETH reach $5000?", "https://example.com/1", "https://example.com/2", "", deadline)
    assert mid == "1"
    
    m_json = contract.get_market("1")
    m = json.loads(m_json)
    
    assert m["status"] == "OPEN"
    assert m["question"] == "Will the price of ETH reach $5000?"
    assert m["resolution_url_1"] == "https://example.com/1"
    assert m["resolution_url_2"] == "https://example.com/2"
    assert m["resolution_url_3"] == ""
    assert int(m["deadline"]) == deadline
    assert m["total_yes"] == "0"
    assert m["total_no"] == "0"

def test_place_bet_success(setup_contract, direct_vm, direct_alice, direct_bob):
    contract, _ = setup_contract
    
    base_time_str = "2026-08-15T00:00:00Z"
    direct_vm.warp(base_time_str)
    base_timestamp = int(datetime.fromisoformat(base_time_str.replace("Z", "+00:00")).timestamp())
    deadline = base_timestamp + 3600
    
    contract.create_market("Will the price of ETH reach $5000?", "https://example.com/1", "", "", deadline)
    
    # Alice bets 100 on YES (before deadline)
    direct_vm.sender = direct_alice
    direct_vm.value = 100
    contract.place_bet("1", "YES")
    
    # Bob bets 200 on NO (before deadline)
    direct_vm.sender = direct_bob
    direct_vm.value = 200
    contract.place_bet("1", "NO")
    
    m = json.loads(contract.get_market("1"))
    assert m["total_yes"] == "100"
    assert m["total_no"] == "200"

def test_place_bet_after_deadline_fails(setup_contract, direct_vm, direct_alice):
    contract, _ = setup_contract
    
    base_time_str = "2026-08-15T00:00:00Z"
    direct_vm.warp(base_time_str)
    base_timestamp = int(datetime.fromisoformat(base_time_str.replace("Z", "+00:00")).timestamp())
    deadline = base_timestamp + 3600
    
    contract.create_market("Will the price of ETH reach $5000?", "https://example.com/1", "", "", deadline)
    
    # Warp to after deadline
    direct_vm.warp("2026-08-15T01:30:00Z") # 1h30m later
    
    direct_vm.sender = direct_alice
    direct_vm.value = 100
    with pytest.raises(Exception) as excinfo:
        contract.place_bet("1", "YES")
    assert_user_error(excinfo, "Market has expired")

def test_resolve_before_deadline_fails(setup_contract, direct_vm):
    contract, _ = setup_contract
    
    base_time_str = "2026-08-15T00:00:00Z"
    direct_vm.warp(base_time_str)
    base_timestamp = int(datetime.fromisoformat(base_time_str.replace("Z", "+00:00")).timestamp())
    deadline = base_timestamp + 3600
    
    contract.create_market("Will the price of ETH reach $5000?", "https://example.com/1", "", "", deadline)
    
    # Warp to before deadline
    direct_vm.warp("2026-08-15T00:30:00Z")
    
    with pytest.raises(Exception) as excinfo:
        contract.resolve("1")
    assert_user_error(excinfo, "Market deadline has not passed yet")

def test_resolve_happy_path(setup_contract, direct_vm, direct_alice, direct_bob):
    contract, owner = setup_contract
    
    base_time_str = "2026-08-15T00:00:00Z"
    direct_vm.warp(base_time_str)
    base_timestamp = int(datetime.fromisoformat(base_time_str.replace("Z", "+00:00")).timestamp())
    deadline = base_timestamp + 3600
    
    contract.create_market("Will Bitcoin price exceed $100k by Jan 2025?", "https://news.com/1", "", "", deadline)
    
    # Place bets
    direct_vm.sender = direct_alice
    direct_vm.value = 100
    contract.place_bet("1", "YES")
    
    direct_vm.sender = direct_bob
    direct_vm.value = 200
    contract.place_bet("1", "NO")
    
    # Warp to after deadline
    direct_vm.warp("2026-08-15T02:00:00Z")
    
    # Mock nondet operations
    direct_vm.mock_web("https://news.com/1", {
        "method": "GET",
        "status": 200,
        "body": "Bitcoin hit $102,000 on Jan 5."
    })
    direct_vm.mock_llm(".*Bitcoin.*", '{"outcome": "YES", "confidence": 95, "reason": "Clearly yes"}')
    
    # Set up gl_call hook to trace value transfers and update balances
    captured_transfers = []
    def gl_call_hook(vm, request):
        if "PostMessage" in request:
            msg = request["PostMessage"]
            recipient = msg["address"]
            val = msg["value"]
            captured_transfers.append((recipient, val))
            
            # Update virtual balance in direct VM context
            recipient_bytes = vm._to_bytes(recipient)
            current_bal = vm._balances.get(recipient_bytes, 0)
            vm.deal(recipient, current_bal + val)
        return {"ok": None}
        
    direct_vm._gl_call_hook = gl_call_hook
    
    # Run resolution
    contract.resolve("1")
    
    # Assert contract state updates
    m = json.loads(contract.get_market("1"))
    assert m["status"] == "RESOLVED"
    assert m["outcome"] == "YES"
    assert int(m["confidence"]) == 95
    
    # Total pool = 300
    # Protocol fee = 6 (2%)
    # Winner gets 294
    assert len(captured_transfers) == 2
    
    # Check fee transfer to owner/treasury
    assert str(captured_transfers[0][0]).lower() == owner.lower()
    assert captured_transfers[0][1] == 6
    
    # Check winning payout to Alice
    assert str(captured_transfers[1][0]).lower() == str(direct_alice).lower()
    assert captured_transfers[1][1] == 294

def test_resolve_low_confidence_refund(setup_contract, direct_vm, direct_alice, direct_bob):
    contract, owner = setup_contract
    
    base_time_str = "2026-08-15T00:00:00Z"
    direct_vm.warp(base_time_str)
    base_timestamp = int(datetime.fromisoformat(base_time_str.replace("Z", "+00:00")).timestamp())
    deadline = base_timestamp + 3600
    
    contract.create_market("Will Bitcoin price exceed $100k by Jan 2025?", "https://news.com/1", "", "", deadline)
    
    # Place bets
    direct_vm.sender = direct_alice
    direct_vm.value = 100
    contract.place_bet("1", "YES")
    
    direct_vm.sender = direct_bob
    direct_vm.value = 200
    contract.place_bet("1", "NO")
    
    # Warp to after deadline
    direct_vm.warp("2026-08-15T02:00:00Z")
    
    # Mock nondet operations (low confidence 45)
    direct_vm.mock_web("https://news.com/1", {
        "method": "GET",
        "status": 200,
        "body": "Bitcoin hit $99,000."
    })
    direct_vm.mock_llm(".*Bitcoin.*", '{"outcome": "YES", "confidence": 45, "reason": "Not sure"}')
    
    captured_transfers = []
    def gl_call_hook(vm, request):
        if "PostMessage" in request:
            msg = request["PostMessage"]
            recipient = msg["address"]
            val = msg["value"]
            captured_transfers.append((recipient, val))
        return {"ok": None}
        
    direct_vm._gl_call_hook = gl_call_hook
    
    # Run resolution
    contract.resolve("1")
    
    m = json.loads(contract.get_market("1"))
    assert m["status"] == "RESOLVED"
    assert m["outcome"] == "INVALID" # Forced to INVALID due to low confidence
    
    # Both players must get full refund (no fees taken)
    assert len(captured_transfers) == 2
    assert str(captured_transfers[0][0]).lower() == str(direct_alice).lower()
    assert captured_transfers[0][1] == 100
    assert str(captured_transfers[1][0]).lower() == str(direct_bob).lower()
    assert captured_transfers[1][1] == 200
