import os
from typing import Dict, List, Literal, Optional, TypedDict, Annotated
from datetime import datetime
import time
import sqlite3
import operator
from typing import Union

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.types import Command
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Environment variables and initialization remain the same
os.environ["OPENAI_API_KEY"] = "key-here"
SLACK_BOT_TOKEN = "token-here"
CHANNEL_ID = "id-here"

# Initialize clients
slack_client = WebClient(token=SLACK_BOT_TOKEN)
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

def bool_reducer(a: bool, b: bool) -> bool:
    """Reducer function for boolean values - returns True if either value is True"""
    return a or b

# Modified State class with proper annotations for concurrent updates
class SlackState(TypedDict):
    messages: Annotated[List[Dict], operator.add]  # Use operator.add as reducer for list concatenation
    last_message: str
    message_count: Annotated[int, operator.add]  # Handle concurrent updates to message count
    valid_response: Annotated[bool, bool_reducer]  # Custom reducer for boolean values
    last_processed_timestamp: Optional[str]

def init_db():
    conn = sqlite3.connect('slack_responses.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS responses
                 (timestamp TEXT, user_id TEXT, message TEXT, thread_ts TEXT DEFAULT '')''')
    conn.commit()
    conn.close()

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

def messenger_node(state: Dict) -> Command[Literal["extractor"]]:
    """Agent responsible for sending AI-generated messages to Slack when needed."""
    current_count = state.get("message_count", 0)
    last_message = state.get("last_message", "")
    
    if not state.get("valid_response", False):
        if current_count == 0:
            prompt = """
            You are a professional communication assistant. Create a polite message requesting 
            the user to update their work email to jhananinallasamy1234@gmail.com.
            The message should be:
            - Professional and courteous
            - Clear about the requirement
            - Brief and to the point
            - End with a clear call to action
            
            Respond with just the message, no additional formatting or explanation.
            """
        else:
            prompt = f"""
            You are a professional communication assistant. The user's last message was: "{last_message}"
            
            This response wasn't what we were looking for regarding the email update request.
            Create a concise follow-up message that:
            - Acknowledges their response politely
            - Asks about the Technical issues preventing update   (if any) 
            - Clarifies that we specifically need them to update their work email
            - Reminds them of the email address (jhananinallasamy1234@gmail.com)
            - Asks if they're having any issues completing this task
            
            Respond with just the message, no additional formatting or explanation.
            """
        
        response = llm.invoke([SystemMessage(content=prompt)])
        message = response.content.strip()
        
        print(f"\nMessenger Agent - Sending message (count: {current_count})")
        send_slack_message(message)
        
        return Command(
            update={
                "messages": [{"role": "assistant", "content": message}],
                "message_count": 1,
                "valid_response": False
            },
            goto="extractor"
        )
    
    return Command(
        update={},
        goto="extractor"
    )

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

def validator_node(state: Dict) -> Command[Literal["messenger", "extractor"]]:
    """Agent responsible for validating messages and storing valid ones."""
    print("\n 🏷️  Validator Agent - Checking message...")
    last_message = state.get("last_message")
    
    if not last_message:
        return Command(
            update={},
            goto="extractor"
        )
    
    validation_prompt = f"""
    Analyze this message and determine if it's a relevant response about work updates:
    Message: {last_message}
    
    A message is considered valid if it includes ANY of these:
    - Technical issues preventing update
    - Changes to the project timeline
    - the answer can also be something "yes, i will update soon"
    - it must be relevant to the question "It seems you have not put any email related to work update. Can you please update it soon to jhananinallasamy1234@gmail.com?"
    Any irrelevant messages will be responded with false.
    Is this a valid response? Respond with just 'true' or 'false'.
    """
    
    response = llm.invoke([SystemMessage(content=validation_prompt)])
    is_valid = 'true' in response.content.lower()
    print(f" 📍 Message validation result: {is_valid}")
    
    if is_valid:
        conn = sqlite3.connect('slack_responses.db')
        c = conn.cursor()
        timestamp = datetime.now().isoformat()
        thread_ts = state.get("last_processed_timestamp", "")
        
        c.execute("INSERT INTO responses (timestamp, user_id, message, thread_ts) VALUES (?, ?, ?, ?)",
                 (timestamp, CHANNEL_ID, last_message, thread_ts))
        
        conn.commit()
        conn.close()
        print("Valid response stored in database")
        
        return Command(
            update={"valid_response": True},
            goto="extractor"
        )
    
    print("Invalid response - continuing loop")
    return Command(
        update={"valid_response": False},
        goto="messenger"
    )

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
    print("\n🖌 Starting Slack validation system...")
    graph = build_graph()
    
    initial_state = {
        "messages": [],
        "last_message": "",
        "message_count": 0,
        "valid_response": False,
        "last_processed_timestamp": None
    }
    
    print("\n 🔁 Initiating continuous monitoring loop...")
    for state in graph.stream(initial_state, {"recursion_limit": None}):
        print("\nCurrent State:")
        print(f"Message count: {state.get('message_count', 0)}")
        print(f"Last message: {state.get('last_message', 'None')}")
        print(f"Valid response: {state.get('valid_response', False)}")
        print("---")

if __name__ == "__main__":
    init_db()
    main()
