# Slack Validation Workflow System

This project implements a Slack-based workflow system to automate the process of collecting, validating, and storing user responses. It leverages agent-based nodes for handling message exchanges and utilizes tools such as LangChain, SQLite, and Slack SDK for integration and workflow management.

## Features
- **Automated Slack Messaging:** Sends initial messages and follow-up reminders to users on a Slack channel.
- **Response Extraction:** Retrieves the latest responses from Slack.
- **Validation of Responses:** Uses an AI model (via LangChain and OpenAI) to validate the relevance of responses.
- **SQLite Integration:** Stores valid responses in a local SQLite database.
- **Agent Workflow Management:** Manages tasks through hierarchical agents and state-based transitions.

## Prerequisites
- Python 3.8 or higher
- OpenAI API Key
- Slack Bot Token with permissions to send messages and retrieve conversation history

## Setup

1. **Install Dependencies:**
   ```bash
   pip install langchain-core langchain-openai slack-sdk sqlite3
   ```

2. **Environment Variables:**
   Set the following environment variables in your system or include them in the script:
   - `OPENAI_API_KEY`: Your OpenAI API Key
   - `SLACK_BOT_TOKEN`: Your Slack Bot Token
   - `CHANNEL_ID`: The ID of the Slack channel for communication

3. **Database Initialization:**
   The SQLite database (`slack_responses.db`) is automatically initialized with a table for storing responses:
   ```sql
   CREATE TABLE IF NOT EXISTS responses (
       timestamp TEXT,
       user_id TEXT,
       message TEXT,
       thread_ts TEXT DEFAULT ''
   );
   ```

4. **Run the Script:**
   Execute the main script:
   ```bash
   python script_name.py
   ```

## Workflow Overview

### Agent Nodes
1. **Messenger Node:**
   - Sends messages to Slack.
   - Sends a follow-up message if no valid response is received.

2. **Extractor Node:**
   - Retrieves the most recent message from the Slack channel.

3. **Validator Node:**
   - Validates the retrieved message using an AI model.
   - Determines if the response is valid based on predefined criteria.

### Workflow Transitions
- The workflow begins with the `messenger` node.
- Proceeds to the `extractor` node to fetch a response.
- Moves to the `validator` node to check response validity.
- Loops back to the `messenger` node if the response is invalid.
- Ends the process when a valid response is received.

### Database Interaction
- Valid responses are stored in the `responses` table with the following fields:
  - `timestamp`: Timestamp of the response
  - `user_id`: ID of the user/channel
  - `message`: The message content
  - `thread_ts`: Thread timestamp for message threading (optional)

## Key Functions

### `send_slack_message`
Sends a message to the specified Slack channel.
- Input: Message string
- Output: Success or error message

### `get_last_slack_message`
Retrieves the latest message from the Slack channel.
- Output: The latest message text or `None`

### `messenger_node`
Manages the sending of Slack messages and updates the state.

### `extractor_node`
Extracts the latest Slack message and updates the state.

### `validator_node`
Validates the latest message based on predefined criteria using an AI model.

## Customization
- **Message Content:** Customize the initial and follow-up messages in `messenger_node`.
- **Validation Criteria:** Modify the `validation_prompt` in `validator_node` for different validation rules.

## Example Output
1. **Sending Initial Message:**
   ```plaintext
   Messenger Agent - Sending message (count: 0)
   Sent message to Slack: It seems you have not put any email related to work update...
   ```
2. **Validating Response:**
   ```plaintext
   Validator Agent - Checking message...
   Message validation result: true
   Valid response stored in database
   ```
3. **Final State:**
   ```plaintext
   Current State:
   Message count: 1
   Last message: Here's the work update...
   Valid response: True
   ```

## Notes
- Ensure the bot has permissions to read and write messages in the specified Slack channel.
- Adjust the `time.sleep(60)` in `extractor_node` for different wait times between message checks.

## License
This project is licensed under the MIT License.

