# filter_transactions_comprehensively

**Scope:** 📚 Strategic Skill

**Description:** Comprehensive transaction filtering that checks both sender and receiver fields, handles pagination, and verifies completeness

**When to use:** filter transactions, find payments, search transaction history, get transactions from, transactions between users

## Instructions

Step 1: Understand the filtering criteria:
- Identify what you're looking for (specific users, transaction types, descriptions)
- Note that transactions can have the target user as EITHER sender OR receiver

Step 2: Retrieve ALL transactions:
- Use pagination to get complete transaction history
- Don't stop at first page - check if there are more results
- Typical pattern: keep fetching until you get an empty result or reach a limit

Step 3: Filter systematically:
- Check BOTH sender_id AND receiver_id fields against your criteria
- For description matching, use case-insensitive contains checks
- Keep a running list of matching transactions

Step 4: Verify completeness:
- Count total transactions retrieved vs filtered
- Double-check edge cases (first transaction, last transaction)
- Verify you haven't missed any by reviewing the full list

Step 5: Extract relevant information:
- For each matching transaction, extract all needed fields
- Create a structured list of results
- Verify each result meets ALL criteria

Step 6: Before proceeding to actions, confirm you have the complete set.

---
*Created: 2026-02-19T01:31:30.796273*
*Version: 1*
*Scope: strategic_skill*
