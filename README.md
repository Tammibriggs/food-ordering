# Food Ordering System Client
This is an MCP client to connect to the [family food ordering MCP server](https://github.com/Tammibriggs/food-ordering/tree/mcp-server). 

## Prerequisite
- Latest Python version installed
- Latest version of `uv` installed
- An Anthropic API key from the [Anthropic Console](https://console.anthropic.com/settings/keys)

After getting the Anthropic API key, create a `.env` file in the root directory and add the key: 

```shell
ANTHROPIC_API_KEY=
```

## Running the Client
First, download the [MCP server file](https://github.com/Tammibriggs/food-ordering/blob/mcp-server/server.py) and place it in the root of the MCP client directory. Make sure to follow the instructions in the README of the MCP server to properly set it up. 

Next, run the following command in your terminal:

```shell
uv run client.py server.py
```

You can login with the following username: 
```python
[
  {"username": "joe", "role": "parent"},
  {"username": "jane", "role": "parent"},
  {"username": "henry", "role": "child"},
  {"username": "rose", "role": "child"}
]
```

