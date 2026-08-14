---
name: pagination-handling
description: Handle paginated API responses correctly
---

## Key techniques
- Check response for `next_page` or pagination tokens
- Loop until no more pages: `while next_page: results = api(page=next_page)`
- Aggregate all pages before processing data
- Some APIs use offset/limit instead of page tokens

## Gotchas
- Missing pagination = incomplete data = wrong answer
- Default page size may be small (10-20 items)
- Always check if the task requires ALL items (not just first page)
