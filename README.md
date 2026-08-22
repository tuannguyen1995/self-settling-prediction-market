# Self-Settling Prediction Market 🔮

**Self-Settling Prediction Market** is a fully autonomous decentralized prediction market primitive built natively on GenLayer. The outcomes are resolved automatically by an AI analyzing real-world web data, eliminating the need for centralized oracles or human multi-sig committees.

> **💡 THE PITCH:** "This project is **IMPOSSIBLE** without GenLayer. No other blockchain allows a smart contract to actively browse the web and use LLMs to evaluate subjective outcomes, reach a decentralized consensus, and securely settle financial markets."

---

## 🏗️ Architecture & Features

The project is implemented as a standalone Intelligent Contract with the following characteristics:

1. **Escrow & Market Lifecycle**
   - Manages market creation, bet pooling in GEN tokens, and pro-rata payouts.
   - Restricts resolution until the market deadline has passed.
   - Enforces a strict betting window (no bets allowed after the market deadline).

2. **Autonomous Resolution via GenLayer's Non-Deterministic AI**
   - Leverages `gl.vm.run_nondet` to access the open web dynamically.
   - **Multi-source sampling**: Reads up to 3 different resolution URLs. Gracefully aborts and refunds players if all sources fail.
   - **Multi-LLM sampling**: Prompts the AI twice per resolution and checks for consistent outcomes to prevent hallucinations.
   - **2% Protocol Fee**: Automatically routes a 2% fee to the protocol treasury on successful resolutions.

---

## ⚖️ The Validator's Role, Bound Thresholds & Deterministic Normalization

In GenLayer, non-deterministic operations (like AI execution and web scraping) require a leader-validator consensus model. 

In our contract, the **Validator strictly binds both payout-affecting fields (`outcome` and `confidence threshold bucket`).**

### 1. Bound Confidence Threshold in Validation
- **Outcome Matching**: `leader_outcome == mine_outcome`.
- **Threshold Bucket Matching**: `(leader_confidence >= 60) == (validator_confidence >= 60)`.
- **Strict Validation Constraint**: If a leader proposes a payout outcome (`YES` or `NO`), its confidence **MUST be >= 60**. If `confidence < 60` for a `YES`/`NO` proposal, the validator explicitly rejects the leader's submission (`return False`).

### 2. Deterministic Post-Consensus Normalization
After consensus is reached via `gl.vm.run_nondet`, the smart contract executes a deterministic post-consensus check:
```python
if confidence_int < 60:
    outcome = "INVALID"
```
This guarantees that regardless of LLM variations, any outcome stored with confidence below 60 is strictly normalized to `INVALID`, triggering a full 100% refund for all players and preventing any unauthorized payout.

---

## 🚀 Deployment (StudioNet)

- **Contract Address**: `0xdec93Fa3DD89fF80540E6641343D48A5Ff281D58`
- **Network**: `studionet`

### 🔮 Worked Example (Illustrative call to resolve)

Below is an illustrative worked example demonstrating how the public API and consensus engine evaluate and settle a market.

#### 1. Input:
Suppose we call the public write method `resolve` on a market created with the following parameters:
- **Market ID**: `1`
- **Question**: `"Will Bitcoin price exceed $100k by Jan 2025?"`
- **Resolution URL 1**: `"https://api.coinbase.com/v2/prices/BTC-USD/spot"`
- **Deadline**: `1735689600` (Unix timestamp for January 1, 2025)

At the time of resolution, the coinbase spot price URL responds with the following content:
```json
{
  "data": {
    "base": "BTC",
    "currency": "USD",
    "amount": "102000.00"
  }
}
```

#### 2. Expected Verdict / Output (Resolved State):
The non-deterministic leader and validator nodes dynamically parse the Coinbase web payload and execute the consensus prompt. Because the price $102,000 exceeds $100,000, the market outcome resolves to `YES` and is recorded in the contract storage as follows:
- **Status**: `"RESOLVED"`
- **Outcome**: `"YES"`
- **Confidence**: `"98"` (agreement of 98% confidence from multi-sampling)
- **Reason**: `"Coinbase API reported BTC spot price at $102,000.00 USD, which exceeds the threshold of $100,000."`

#### 3. Payout Execution (Emitted Events):
- A **2% protocol fee** is transferred to the treasury address.
- The remaining pool is distributed **pro-rata** to players who bet on `YES`. (e.g. if Alice bet 100 on YES and Bob bet 200 on NO, Alice receives the full remaining pool of 294 tokens).

---

## 🧪 Testing

The contract comes with a complete automated test suite covering all logic paths, including time-warping via the `transaction_context` config:

To run tests:
```bash
gltest tests/test_prediction_market.py -s
```
