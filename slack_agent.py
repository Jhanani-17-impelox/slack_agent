import os
from datetime import datetime
import time  #For delays in processing loops.
import sqlite3
from typing import Dict, List, Literal, Optional, TypedDict, Annotated, Set, Union
import operator
import json
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.types import Command
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

#------------------------------Environment variables for OpenAI API and Slack bot token------------------------------

os.environ["OPENAI_API_KEY"] = "key-here"
SLACK_BOT_TOKEN = "token-here"
CHANNEL_ID = "channel-here"

# Initialize clients
slack_client = WebClient(token=SLACK_BOT_TOKEN)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

#------------------------------ Reducers combine data during state transitions:-------------------------------------

# bool_reducer: Combines booleans using logical OR.
def bool_reducer(a: bool, b: bool) -> bool:
    return a or b

# dict_reducer: Merges two dictionaries, preferring values from the second.
def dict_reducer(a: Dict, b: Dict) -> Dict:
    """Combines two dictionaries, preferring values from b when keys overlap"""
    return {**a, **b}
 
# set_reducer: Combines two sets using union.
def set_reducer(a: Set, b: Set) -> Set:
    """Combines two sets using union operation"""
    return a.union(b)

# ------------------------------ Represents the structure of the chatbot state with type annotations. ------------------------------
class SlackState(TypedDict):
    messages: Annotated[List[Dict], operator.add]
    last_message: str
    initial_bot_message: str
    current_bot_message: str
    message_count: Annotated[int, operator.add]
    valid_response: Annotated[bool, bool_reducer]
    last_processed_timestamp: Optional[str]
    conversation_started: bool
    collected_info: Annotated[Dict[str, str], dict_reducer]  # Added reducer
    missing_info: Annotated[Set[str], set_reducer]  # Added reducer

# ------------------------------ Database Initialization ------------------------------
def init_db():
    """Initialize database with enhanced schema"""
    conn = sqlite3.connect('slack_responses.db')
    c = conn.cursor()
    
    # # Drop existing tables
    # c.execute('DROP TABLE IF EXISTS responses')
    # c.execute('DROP TABLE IF EXISTS conversation_state')
    
    # Create responses table with enhanced schema
    # responses: User and bot messages, timestamp, and collected/missing information.

    c.execute('''CREATE TABLE IF NOT EXISTS responses
                 (timestamp TEXT,
                  user_id TEXT,
                  bot_message TEXT,
                  user_message TEXT,
                  thread_ts TEXT,
                  collected_info TEXT,  -- JSON string of collected information
                  missing_info TEXT)''') 
    
    # Create conversation state table
    # conversation_state: Tracks the overall state of a conversation.

    c.execute('''CREATE TABLE IF NOT EXISTS conversation_state
                 (channel_id TEXT PRIMARY KEY,
                  collected_info TEXT,
                  missing_info TEXT,
                  last_valid_response TEXT,
                  conversation_complete INTEGER)''')
    
    conn.commit()
    conn.close()

#  ------------------------------ ------------------------------ ------------------------------

# Sends a message to Slack and handles errors using SlackApiError.
def send_slack_message(message: str) -> str:
    try:
        response = slack_client.chat_postMessage(
            channel=CHANNEL_ID,
            text=message
        )
        print(f"Sent message to Slack 📌📨: {message}")
        return "✔️ Message sent successfully"
    except SlackApiError as e:
        print(f"❌ Error sending message: {e.response['error']}")
        return f"⚠️ Error sending message: {e.response['error']}"

# Retrieve Slack Messages
# Fetches the most recent Slack message, ensuring it hasn't already been processed.

def get_last_slack_message(last_processed_ts: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    try:
        response = slack_client.conversations_history(
            channel=CHANNEL_ID,
            limit=1
        )
        if response['messages']:
            message = response['messages'][0]['text']
            timestamp = response['messages'][0]['ts']
            
            if not last_processed_ts or timestamp > last_processed_ts:
                print(f"📩 Retrieved new message from Slack: {message}")
                return message, timestamp
            
            print("No new messages since last check")
            return None, last_processed_ts
            
        print("❗No messages found in Slack")
        return None, last_processed_ts
    except SlackApiError as e:
        print(f"🚨 Error getting message: {str(e)}")
        return None, last_processed_ts

# ------------------------------- Conversation State Management (retrieves and updates in the database)------------------------------------

# get_conversation_state: Retrieves the current state of the conversation from the database.
def get_conversation_state() -> Dict:
    """Retrieve current conversation state from database"""
    conn = sqlite3.connect('slack_responses.db')
    c = conn.cursor()
    
    c.execute('SELECT collected_info, missing_info FROM conversation_state WHERE channel_id = ?', (CHANNEL_ID,))
    result = c.fetchone()
    
    if result:
        collected_info = json.loads(result[0]) if result[0] else {}
        missing_info = set(json.loads(result[1])) if result[1] else set()
    else:
        collected_info = {}
        missing_info = {'technical_issues', 'update_confirmation', 'email_acknowledgment'}
        c.execute(
            'INSERT INTO conversation_state (channel_id, collected_info, missing_info, conversation_complete) VALUES (?, ?, ?, 0)',
            (CHANNEL_ID, json.dumps(collected_info), json.dumps(list(missing_info)))
        )
        conn.commit()
    
    conn.close()
    return {'collected_info': collected_info, 'missing_info': missing_info}

def update_conversation_state(collected_info: Dict, missing_info: Set):
    """Update conversation state in database"""
    conn = sqlite3.connect('slack_responses.db')
    c = conn.cursor()
    
    c.execute(
        'UPDATE conversation_state SET collected_info = ?, missing_info = ? WHERE channel_id = ?',
        (json.dumps(collected_info), json.dumps(list(missing_info)), CHANNEL_ID)
    )
    
    conn.commit()
    conn.close()

# ------------------------------- ------------------------------- ------------------------------- ------------------------------- 
# Generates and sends a Slack message based on the current state using OpenAI.

# Sends context-aware messages to the user.
# Dynamically generates prompts using the current conversation state.
# If all required information is collected, sends a thank-you message.

def messenger_node(state: Dict) -> Command[Literal["extractor"]]:
    """Enhanced messenger agent with dynamic message generation"""
    current_count = state.get("message_count", 0)
    last_message = state.get("last_message", "")
    conversation_started = state.get("conversation_started", False)
    conv_state = get_conversation_state() #get_conversation_state: Retrieves the current state of the conversation from the database.

    
    if not state.get("valid_response", False) or conv_state['missing_info']:
        if not conversation_started:
            prompt = """
            Create a polite message requesting the user to update their work email to jhananinallasamy1234@gmail.com.
            The message should be professional, clear, and end with a call to action.
            Include a request for information about any technical issues they might be facing.
            """
        else:
            missing_items = conv_state['missing_info']
            collected_items = conv_state['collected_info']
            
            prompt = f"""
            Based on the conversation so far:
            - Previous user message: "{last_message}"
            - Already collected info: {json.dumps(collected_items)}
            - Missing info: {list(missing_items)}
            
            Create a follow-up message that:
            1. Acknowledges any information already provided
            2. Specifically asks about the missing information: {list(missing_items)}
            3. Maintains a professional and helpful tone
            4. Includes the email address (jhananinallasamy1234@gmail.com) if email acknowledgment is still needed
            
            The message should be concise and focused on gathering the remaining information.
            """
    else:
        # All information collected - send thank you message
        prompt = """
        Create a polite thank you message that:
        1. Acknowledges the user's cooperation
        2. Confirms that all required information has been received
        3. Provides a professional closing to the conversation
        """
    
    response = llm.invoke([SystemMessage(content=prompt)])
    message = response.content.strip()
    
    print(f"\nMessenger Agent - Sending message (count: {current_count})")
    send_slack_message(message)
    
    updates = {
        "messages": [{"role": "assistant", "content": message}],
        "message_count": 1,
        "valid_response": False,
        "current_bot_message": message,
        "conversation_started": True,
        "collected_info": conv_state['collected_info'],
        "missing_info": conv_state['missing_info']
    }
    
    if not conversation_started:
        updates["initial_bot_message"] = message
    
    return Command(update=updates, goto="extractor")

# ------------------------------- ------------------------------- ------------------------------- ------------------------------- 
# Periodically checks for new Slack messages and updates the state.
def extractor_node(state: Dict) -> Command[Literal["validator"]]:
    """Agent responsible for continuously extracting messages from Slack."""
    print("\n 🔎 Extractor Agent - Checking for new messages...")
    time.sleep(30)
    
    last_processed_ts = state.get("last_processed_timestamp")
    last_message, new_timestamp = get_last_slack_message(last_processed_ts)
    
    if last_message:
        return Command(
            update={
                "messages": [{"role": "user", "content": last_message}],
                "last_message": last_message,
                "last_processed_timestamp": new_timestamp
            },
            goto="validator"
        )
    
    return Command(
        update={},
        goto="validator"
    )

# ------------------------------- ------------------------------- ------------------------------- ------------------------------- 
# Validates and extracts information from the user's response using OpenAI.

# Validates the user's response using OpenAI GPT.
# Extracts relevant information from the response.
# Updates the collected_info and missing_info fields in the conversation state.
# Stores the validation results and conversation data in the database.


def validator_node(state: Dict) -> Command[Literal["messenger", "extractor"]]:
    """Enhanced validator agent with improved information extraction and robust JSON parsing"""
    print("\n 🏷️  Validator Agent - Analyzing message...")
    last_message = state.get("last_message")
    initial_bot_message = state.get("initial_bot_message", "")
    current_bot_message = state.get("current_bot_message", "")
    
    if not last_message:
        return Command(update={}, goto="extractor")
    
    # Get current conversation state
    conv_state = get_conversation_state()
    collected_info = conv_state['collected_info']
    missing_info = conv_state['missing_info']

    
    validation_prompt = f"""
    You are a JSON response validator. Your task is to analyze this conversation and extract information.
    
    Context:
    Initial request: {initial_bot_message}
    Current bot message: {current_bot_message}
    User's response: {last_message}
    Previously collected info: {json.dumps(collected_info)}
    Missing info: {list(missing_info)}
    
    Instructions:
    1. Analyze if the user's response contains valid information for any of these categories:
       - Technical issues (any mentioned problems preventing update)
       - Update confirmation (clear intent to update email)
       - Email acknowledgment (specifically about jhananinallasamy1234@gmail.com)
    
    2. Return ONLY a JSON object with exactly this structure:
    {{
        "valid": boolean,
        "technical_issues": string or null,
        "update_confirmation": string or null,
        "email_acknowledgment": string or null,
        "reason_if_invalid": string or null
    }}
    
    3. Format rules:
    - Use true/false (lowercase) for the valid field
    - Use null (not None or empty string) for missing information
    - Include extracted text as strings for found information
    - Always include all fields
    - No extra fields or comments
    
    Example valid response:
    {{"valid":true,"technical_issues":"firewall blocking access","update_confirmation":null,"email_acknowledgment":null,"reason_if_invalid":null}}
    """
    
    try:
        response = llm.invoke([SystemMessage(content=validation_prompt)])
        # Clean the response to handle potential formatting issues
        cleaned_response = response.content.strip()
        # Remove any markdown formatting if present
        if cleaned_response.startswith("```json"):
            cleaned_response = cleaned_response.replace("```json", "").replace("```", "")
        # Remove any potential natural language before or after the JSON
        cleaned_response = cleaned_response[cleaned_response.find("{"):cleaned_response.rfind("}")+1]
        
        validation_result = json.loads(cleaned_response)
        
        # Validate required fields
        required_fields = {'valid', 'technical_issues', 'update_confirmation', 'email_acknowledgment', 'reason_if_invalid'}
        if not all(field in validation_result for field in required_fields):
            print("Missing required fields in validation response")
            return Command(update={"valid_response": False}, goto="messenger")
        
        is_valid = validation_result.get('valid', False)
        
    except (json.JSONDecodeError, KeyError, Exception) as e:
        print(f"Error processing validation response: {str(e)}")
        return Command(update={"valid_response": False}, goto="messenger")
    
    if is_valid:
        # Update collected and missing information
        new_collected_info = {}
        new_missing_info = set(missing_info)  # Create a copy
        
        for key in ['technical_issues', 'update_confirmation', 'email_acknowledgment']:
            if validation_result.get(key):
                new_collected_info[key] = validation_result[key]
                new_missing_info.discard(key)
        
        # Store in database (database operations remain the same)
        conn = sqlite3.connect('slack_responses.db')
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        thread_ts = state.get("last_processed_timestamp", "")
        
        c.execute("""
            INSERT INTO responses 
            (timestamp, user_id, bot_message, user_message, thread_ts, collected_info, missing_info) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp, 
            CHANNEL_ID, 
            current_bot_message, 
            last_message, 
            thread_ts,
            json.dumps(new_collected_info),
            json.dumps(list(new_missing_info))
        ))
        
        conn.commit()
        conn.close()
        
        # Update conversation state
        # Updates the database with newly collected or missing information.
        update_conversation_state(new_collected_info, new_missing_info)
        
        if not new_missing_info:
            print("✅ All information collected!")
            return Command(
                update={
                    "valid_response": True,
                    "collected_info": new_collected_info,
                    "missing_info": new_missing_info
                },
                goto="messenger"
            )
        
        print("⚡ Partial information collected - continuing conversation")
        return Command(
            update={
                "valid_response": False,
                "collected_info": new_collected_info,
                "missing_info": new_missing_info
            },
            goto="messenger"
        )
    
    print(f"❌ Invalid response - {validation_result.get('reason_if_invalid', 'No reason provided')}")
    return Command(
        update={
            "valid_response": False,
            "collected_info": {},  # Empty dict instead of reusing existing
            "missing_info": missing_info  # Reuse existing missing_info
        },
        goto="messenger"
    )

# -------------------------------Graph Definition---------------------------------------------
def build_graph() -> StateGraph:
    print("Building agent graph 📊📈...")
    builder = StateGraph(SlackState)
    
    builder.add_node("messenger", messenger_node)
    builder.add_node("extractor", extractor_node)
    builder.add_node("validator", validator_node)
    
    builder.add_edge(START, "messenger")
    builder.add_edge("messenger", "extractor")
    builder.add_edge("extractor", "validator")
    builder.add_edge("validator", "messenger")
    builder.add_edge("validator", "extractor")
    
    return builder.compile()

def main():
    print("\n🖌 Starting enhanced Slack validation system...")
    graph = build_graph()
    
    initial_state = {
        "messages": [],
        "last_message": "",
        "initial_bot_message": "",
        "current_bot_message": "",
        "message_count": 0,
        "valid_response": False,
        "last_processed_timestamp": None,
        "conversation_started": False,
        "collected_info": {},
        "missing_info": {'technical_issues', 'update_confirmation', 'email_acknowledgment'}
    }
    
    print("\n 🔁 Initiating continuous monitoring loop...")
    for state in graph.stream(initial_state, {"recursion_limit": None}):
        print("\nCurrent State:")
        print(f"Message count: {state.get('message_count', 0)}")
        print(f"Collected info: {state.get('collected_info', {})}")
        print(f"Missing info: {state.get('missing_info', set())}")
        print(f"Last user message: {state.get('last_message', 'None')}")
        print(f"Valid response: {state.get('valid_response', False)}")
        print("---")

if __name__ == "__main__":
    init_db()
    main()
