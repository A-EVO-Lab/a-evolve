# paginate_api_results_usage

**Description:** 

**When to use:** N/A

## Instructions

# How to Use the paginate_api_results Tool

## When to Use
- When an API endpoint returns paginated results (has 'next' field or pagination parameters)
- When you need to collect ALL items from a paginated API response
- After you've made an initial API call and seen pagination indicators

## Correct Invocation Syntax

**IMPORTANT: Call the tool using standard Python function syntax:**

```python
result = paginate_api_results(
    api_function_name='appworld_api_call',
    initial_params={
        'app_name': 'spotify',
        'endpoint': '/me/tracks',
        'method': 'GET',
        'params': {'limit': 50}
    },
    pagination_key='next',
    max_pages=100
)
```

## Parameters (in order)
1. `api_function_name` (str, required): The name of the API calling function (usually 'appworld_api_call')
2. `pagination_key` (str, optional): The JSON key that contains the next page URL (default: 'next')
3. `max_pages` (int, optional): Maximum number of pages to fetch (default: 50)
4. `initial_params` (dict, optional): Dictionary containing all parameters for the first API call (default: {})

## Return Value
Returns a dict with:
- `all_items`: List of all collected items from all pages
- `total_pages`: Number of pages fetched
- `total_items`: Total number of items collected
- `error`: (if any error occurred) Error message

## Complete Example

```python
# Step 1: Make initial call to see pagination structure
initial_response = appworld_api_call(
    app_name='spotify',
    endpoint='/me/tracks',
    method='GET',
    params={'limit': 50}
)

# Step 2: If paginated, use the tool to get all items
if 'next' in initial_response:
    all_data = paginate_api_results(
        api_function_name='appworld_api_call',
        initial_params={
            'app_name': 'spotify',
            'endpoint': '/me/tracks',
            'method': 'GET',
            'params': {'limit': 50}
        },
        pagination_key='next',
        max_pages=100
    )
    print(f"Collected {all_data['total_items']} items across {all_data['total_pages']} pages")
    items = all_data['all_items']
```

## Common Patterns

### Spotify API (uses 'next' URL)
```python
result = paginate_api_results(
    api_function_name='appworld_api_call',
    initial_params={'app_name': 'spotify', 'endpoint': '/me/playlists', 'method': 'GET'},
    pagination_key='next'
)
```

### APIs with token-based pagination
```python
result = paginate_api_results(
    api_function_name='appworld_api_call',
    initial_params={'app_name': 'some_app', 'endpoint': '/items', 'method': 'GET'},
    pagination_key='nextPageToken'
)
```

## Error Handling
The tool returns partial results even if an error occurs during pagination. Always check:
- `error` field for any errors
- `total_items` to see how many items were collected
- `all_items` for the actual data

---
*Updated: 2026-02-19T03:06:16.547820*
*Version: 2*
