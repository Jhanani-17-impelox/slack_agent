import os
from typing import Dict, List, Literal, Optional, TypedDict
from datetime import datetime
import time
import sqlite3

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.types import Command
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Set environment variables
os.environ["OPENAI_API_KEY"] = "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"
SLACK_BOT_TOKEN = "xoxb-1358024980401-5367047827203-LYEuUq70P1DpMlEiaTGLpQ0z"
CHANNEL_ID = "C07CJUKRZMK"


# Initialize Slack client
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('slack_responses.db')                                                # Connect to SQLite database
    c = conn.cursor()                                                                           # Create a cursor object for executing SQL commands
    # Ensure thread_ts has a default value of '' to avoid NULL issues
    c.execute('''CREATE TABLE IF NOT EXISTS responses
                 (timestamp TEXT, user_id TEXT, message TEXT, thread_ts TEXT DEFAULT '')''')
    conn.commit()
    conn.close()

init_db()

# Initialize LLM
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)


# -----------------------------------------Defining the State for the Slack Workflow:-------------------------------------------------------
# State class definition
class SlackState(TypedDict):
    messages: List[Dict]                                                                        # List of messages in the conversation    
    last_message: Optional[str]                                                                 # The last received message, or None
    message_count: int                                                                          # Number of messages sent    
    valid_response: bool                                                                        # Whether the response is considered valid


#------------------------- Sends a message to the Slack channel specified by CHANNEL_ID using Slack's chat_postMessage method.---------------
#-------------------------  If an error occurs, it catches the SlackApiError and prints the error--------------------------------------------


# Agent Functions
def send_slack_message(message: str) -> str:
    try:
        response = slack_client.chat_postMessage(
            channel=CHANNEL_ID,
            text=message                                                                        #tells the Slack client to post the content stored in the message variable to the Slack channel (CHANNEL_ID).
        )
        print(f"Sent message to Slack: {message}")
        return "Message sent successfully"
    except SlackApiError as e:
        print(f"Error sending message: {e.response['error']}")
        return f"Error sending message: {e.response['error']}"


#------------------------Retrieves the most recent message from the Slack channel using conversations_history----------------------------------
#------------------------It returns the message text or None if no messages are found or an error occurs---------------------------------------

def get_last_slack_message() -> Optional[str]:
    try:
        response = slack_client.conversations_history(
            channel=CHANNEL_ID,
            limit=1
        )
        if response['messages']:
            message = response['messages'][0]['text']
            print(f"Retrieved message from Slack: {message}")
            return message
        print("No messages found in Slack")
        return None
    except SlackApiError as e:
        print(f"Error getting message: {str(e)}")
        return None

# Agent Nodes
def messenger_node(state: Dict) -> Command[Literal["extractor"]]:
    """Agent responsible for sending messages to Slack."""
    # Get the current count and ensure it's properly incremented
    current_count = state.get("message_count", 0)
    
    if state.get("valid_response", False):
        print("Valid response received. No need to send another message.")
        return Command(
            update=state,  # Keep existing state
            goto=END  # End the loop if a valid response was received
        )
    
    # If the message count is 0, ask for the work update; otherwise, send the follow-up message
    if current_count == 0:
        message = "It seems you have not put any email related to work update. Can you please update it soon to jhananinallasamy1234@gmail.com?"
    else:
        message = "It seems you didn't reply properly for the previous message, can you let me know what is the issue you are facing right now?"
    
    print(f"\nMessenger Agent - Sending message (count: {current_count})")
    send_slack_message(message)
    
    # Create new state with incremented count
    new_state = state.copy()  # Copy existing state
    new_state["messages"] = state.get("messages", []) + [{"role": "assistant", "content": message}]
    new_state["message_count"] = current_count + 1
    new_state["valid_response"] = False  # Ensure valid response is false until validated
    
    return Command(
        update=new_state,
        goto="extractor"  # Proceed to extractor node for message extraction
    )


def extractor_node(state: Dict) -> Command[Literal["validator"]]:
    """Agent responsible for extracting the last message from Slack."""
    print("\nExtractor Agent - Waiting for response...")
    time.sleep(60)  # Wait for 1 minute
    
    last_message = get_last_slack_message()
    print(f"Extracted message: {last_message}")
    
    # Maintain existing state while updating the last message
    new_state = state.copy()  # Copy existing state
    new_state["last_message"] = last_message
    
    if last_message:
        new_state["messages"] = state.get("messages", []) + [{"role": "user", "content": last_message}]
    
    return Command(
        update=new_state,
        goto="validator"   #goto keyword is used as part of the return value of each agent function (node), and it defines the next node or state to transition to in the workflow.
    )


def validator_node(state: Dict) -> Command[Literal["messenger", "__end__"]]:
    """Agent responsible for validating the message content."""
    print("\nValidator Agent - Checking message...")
    last_message = state.get("last_message")
    
    if not last_message:
        print("No message to validate")
        return Command(
            update=state,  # Keep existing state
            goto="messenger"  # Go to messenger if no message was received
        )
    
    validation_prompt = f"""
    Analyze this message and determine if it's a relevant response about work updates:
    Message: {last_message}
    
    A message is considered valid if it includes ANY of these:
    - Clear acknowledgment of the update request
    - Explanation of current work status
    - Explanation for delays
    - Timeline for updates
    - Technical issues preventing update
    
    Is this a valid response? Respond with just 'true' or 'false'.
    """
    
    response = llm.invoke([SystemMessage(content=validation_prompt)])
    is_valid = 'true' in response.content.lower()
    print(f"Message validation result: {is_valid}")
    
    if is_valid:
        # Make sure you're inserting 5 values (timestamp, user_id, message, thread_ts, and status)
        conn = sqlite3.connect('slack_responses.db')
        c = conn.cursor()
        timestamp = datetime.now().isoformat()  # Add current timestamp
        user_id = CHANNEL_ID  # Assuming you have a variable CHANNEL_ID for user_id
        thread_ts = "some_thread_ts"  # Use actual thread_ts if available, otherwise put a placeholder
        status = "valid"  # Example status column
        
        # Insert all necessary values (5 columns in this example)
        c.execute("INSERT INTO responses (timestamp, user_id, message, thread_ts) VALUES (?, ?, ?, ?)",
                   (timestamp, user_id, last_message, thread_ts))

        conn.commit()
        conn.close()
        print("Valid response stored in database")
        
        new_state = state.copy()
        new_state["valid_response"] = True
        return Command(
            update=new_state,
            goto=END  # End the loop if the response is valid
        )
    
    print("Invalid response - continuing loop")
    return Command(
        update=state,  # Keep existing state
        goto="messenger"  # Go back to messenger if the response is invalid
    )


# Build the graph
def build_graph() -> StateGraph:
    print("Building agent graph...")
    builder = StateGraph(SlackState)
    
    # Add nodes
    builder.add_node("messenger", messenger_node)
    builder.add_node("extractor", extractor_node)
    builder.add_node("validator", validator_node)
    
    # Add edges
    builder.add_edge(START, "messenger")
    builder.add_edge("messenger", "extractor")
    builder.add_edge("extractor", "validator")
    builder.add_edge("validator", "messenger")
    
    return builder.compile()

# Main execution
def main():
    print("Starting Slack validation system...")
    graph = build_graph()
    
    # Initial state with all required fields
    initial_state = {
        "messages": [],
        "last_message": None,
        "message_count": 0,
        "valid_response": False
    }
    
    print("\nInitiating message loop...")
    # Run the graph
    for state in graph.stream(initial_state, {"recursion_limit": 100}):
        print("\nCurrent State:")
        print(f"Message count: {state.get('message_count', 0)}")
        print(f"Last message: {state.get('last_message', 'None')}")
        print(f"Valid response: {state.get('valid_response', False)}")
        print("---")
        
        if state.get("valid_response", False):
            print("\nValid response received and stored in database.")
            print(f"Final message: {state.get('last_message')}")
            break

if __name__ == "__main__":
    main()
