# Praktor for Home Assistant

A custom integration that connects Home Assistant to **Praktor**, a chat service
running on your own network. It exposes Praktor in two ways:

- **A conversation agent**, so Praktor can be selected as the agent behind
  Assist or used as an agent in a pipeline.
- **The `praktor.ask` service**, so automations and scripts can send a message
  to a Praktor agent and use the reply.

Praktor routes each message to one of the agents that are allowed for your chat
token, and returns the agent's reply.

## Installation

### HACS (custom repository)

1. Make sure [HACS](https://hacs.xyz/) is installed.
2. In Home Assistant, go to **HACS → Integrations → ⋮ → Custom repositories**.
3. Add `https://github.com/basigabri/hacs-praktor` and choose the category
   **Integration**.
4. Search for **Praktor** in HACS, open it and click **Download**.
5. Restart Home Assistant.

### Manual

Copy the `custom_components/praktor` directory into the `custom_components`
directory of your Home Assistant configuration, then restart Home Assistant.

## Setup

You need two things from the Praktor App:

- the **chat token**, and
- the **URL** that the App logs when it starts (for example
  `http://local-praktor:8080`).

Then, in Home Assistant:

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **Praktor**.
3. Enter the URL and the chat token.

The integration checks the URL and the token by sending an empty message to
`/api/chat`: Praktor answers with `400 message is required` for a valid token
and with `401` for an invalid one. Nothing else is ever called.

The URL must use `http` or `https`, and must not contain credentials, a query
string or a fragment.

### Options

After adding the integration, open **Configure** on the Praktor entry:

| Option    | Default | Description                                                                                 |
| --------- | ------- | ------------------------------------------------------------------------------------------- |
| `agent`   | *(empty)* | Agent to send messages to. Empty means Praktor's first allowed agent (by default `claude`). |
| `timeout` | `120`   | How long Praktor may wait for the agent to reply, in seconds (10–600).                       |

Changing an option reloads the integration.

If Praktor rejects the chat token while you use Assist, Home Assistant starts
a reauthentication flow and asks for a new token; the URL and options are
kept. An agent that isn't allowed for the token is reported as such and
doesn't ask for a new token.

## Using it from Assist

Select **Praktor** as the conversation agent of an Assist pipeline, or set it as
the agent of the `conversation.process` action. The integration reports that it
supports all languages, and forwards your text unchanged to Praktor.

## The `praktor.ask` service

`praktor.ask` sends a message and returns `{"agent": ..., "reply": ...}`. It
always returns a response, so it must be used with `response_variable`.

| Field             | Required | Description                                                              |
| ----------------- | -------- | ------------------------------------------------------------------------ |
| `message`         | yes      | The message to send.                                                      |
| `agent`           | no       | Agent to ask. Defaults to the configured agent, or the first allowed one. |
| `timeout`         | no       | Timeout in seconds (1–600). Defaults to the configured timeout.            |
| `config_entry_id` | no       | Which Praktor entry to use. Defaults to the first loaded one.             |

### Example: ask in an automation

Don't send security-related events or states (locks, alarm, garage, cameras,
presence) to a cloud model. Use it for things like this:

```yaml
automation:
  - alias: "Morning briefing"
    triggers:
      - trigger: time
        at: "07:30:00"
    actions:
      - action: praktor.ask
        data:
          message: "Give me a two-sentence motivational quote for today."
        response_variable: praktor_reply
      - action: persistent_notification.create
        data:
          title: "Praktor"
          message: "{{ praktor_reply.reply }}"
```

### Example: ask from Developer Tools

```yaml
action: praktor.ask
data:
  message: "Summarize the state of the house in one sentence."
  agent: claude
  timeout: 120
response_variable: praktor_reply
```

The reply is then available as `{{ praktor_reply.reply }}` and the agent that
answered as `{{ praktor_reply.agent }}`.

## Privacy

- Requests go **only** to the Praktor instance you configured, and Praktor
  forwards them **only** to the agents that are allowed for your chat token. By
  default that is `claude` — `deepseek` is never used unless the token
  explicitly allows it.
- The integration talks to exactly one endpoint, `POST /api/chat`. No message is
  sent anywhere else, and nothing is sent when you are not using the
  conversation agent or the service.
- The chat token is stored in the Home Assistant config entry, is marked as a
  password in the config flow, and is never written to the log.

## Security notes

- **The chat token only grants chat.** It cannot be used to change Praktor's
  configuration, list other endpoints or control any device.
- **Redirects are never followed.** If Praktor answers with a redirect, the
  integration treats it as an error, so the token can never be carried to
  another host.
- **The agent has no device control.** Praktor agents return text only; this
  integration exposes that text as a reply. It never lets an agent call Home
  Assistant services or change entities on its own.
- The URL is required to be a plain `http`/`https` URL without embedded
  credentials, a query string or a fragment.

## Development

- `ruff check . && ruff format --check .` (config in `ruff.toml`, with the
  async and security rule sets).
- `tests/ha_e2e.sh` runs the integration in a real Home Assistant container
  against a strict mock of Praktor's chat API. It covers the config flow,
  Assist, `praktor.ask`, the options flow, error replies, and checks that
  only `POST /api/chat` is called and that the token never appears in the
  logs. CI runs it against the pinned and the current stable Home Assistant,
  together with hassfest and the HACS validation.
