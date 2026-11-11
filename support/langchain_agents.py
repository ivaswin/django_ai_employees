from django.conf import settings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from .langchain_tools import get_order_details,get_refund_history,check_delivery_status,search_knowledge_base,get_customer_risk_profile
from .agents import SUPPORT_SYSTEM_PROMPT,MANAGER_SYSTEM_PROMPT,RISK_SYSTEM_PROMPT
from langgraph.checkpoint.memory import InMemorySaver
from .models import Conversation,AgentLog
from langchain.agents.middleware import wrap_tool_call
from .event_queue import publish,DONE
from langchain.tools import tool

# Initialize anthropic client
llm = ChatGoogleGenerativeAI(
    model=settings.GEMINI_MODEL,
    api_key=settings.GEMINI_API_KEY,
)

SUPPORT_TOOLS = [get_order_details,get_refund_history,check_delivery_status,search_knowledge_base]

checkpointer = InMemorySaver()






def run_support_agent_langchain(user_message,conversation_id,order_id,user_id):
    conv = Conversation.objects.get(id=conversation_id)
    @tool
    def escalate_to_manager(case_summary:str)->dict:
        """Escalate the case to the manager for a refund decision. Use this when customer requests a refund or compensation. Prepare a detailed case summary including order details, refund history and customer complaint before escalating."""
        return run_manager_agent_langchain(case_summary,conversation_id)
        
    config = {"configurable": {"thread_id": str(conversation_id)}}
    contextual_message = f"[Context:This conversation is about the order #{order_id}, user:{user_id}] {user_message}"

    @wrap_tool_call
    def log_tool_calls_middleware(request,handler):
        # Before tool call
        tool_name = request.tool_call["name"]
        tool_args = request.tool_call["args"]
        # log the tool call
        AgentLog.objects.create(conversation =conv,event_type="tool_call",message=f"Calling tool{tool_name} with input{tool_args}")
        # publishing tool call
        event = {"type":"tool_call","message":f"Calling tool{tool_name} with input{tool_args}"}
        publish(conversation_id,event)

        # -- tool call --
        result = handler(request)
        
        # After tool call
        # log tool result
        print("tool_result ------>",result)
        AgentLog.objects.create(conversation =conv,event_type="tool_result",message=f"{tool_name} returned : {result.content}")
        # publishing tool result
        event = {"type":"tool_result","message":f"{tool_name} returned : {result.content}"}
        publish(conversation_id,event)
       
        return result

    support_agent = create_agent (
        model = llm,
        tools = SUPPORT_TOOLS +[escalate_to_manager],
        system_prompt=SUPPORT_SYSTEM_PROMPT,
        checkpointer=checkpointer,
        middleware=[log_tool_calls_middleware],

    )
    result = support_agent.invoke(
        {"messages": [{"role": "user", "content": contextual_message}]},
        config=config,
            )
    # print("result==>",result)
    final_reply =  result["messages"][-1].content[0]["text"]
    #print("finala replay ------------------------->>>>>>>>>>>>",final_reply)
    # Log final replay to agent log
    AgentLog.objects.create(conversation =conv,event_type="final",message=final_reply)
    # publish final replay
    event = {"type":"final","message":final_reply}
    publish(conversation_id,event)
    publish(conversation_id,DONE)
    return final_reply


def run_manager_agent_langchain(case_summary,conversation_id):
    conv = Conversation.objects.get(id=conversation_id)
    @tool
    def assess_fraud_risk(user_id:int)->dict:
        """ Consult the risk agent to assess fraud risk for a customer. Use this when refund request looks suspicious or customer has multiple refund requests. Pass the user_id to get a risk verdict"""
        return run_risk_agent_langchain(user_id,conversation_id)
    
    event = {"type":"manager","message":f"Case received for review: {case_summary[:200]}"}
    publish(conversation_id,event)
    AgentLog.objects.create(conversation=conv,event_type="manager",message=f"Case received for review: {case_summary[:200]}")

    # middleware
    @wrap_tool_call
    def manager_log_tool_calls_middleware(request,handler):
        # before calling the tool
        event = {"type":"manager","message":"Consulting risk agent for fraud assesment.."}
        publish(conversation_id,event)

        # log consulting risk agent
        AgentLog.objects.create(conversation=conv,event_type="manager",message="Consulting risk agent for fraud assesment..")

        result = handler(request) # tool will be executed here

        # after calling the middleware
        
        return result


    manager_agent = create_agent(
        model = llm,
        tools =[assess_fraud_risk],
        system_prompt=MANAGER_SYSTEM_PROMPT,
        middleware=[manager_log_tool_calls_middleware]     

    )

    result = manager_agent.invoke({
        "messages" : [{"role":"user","content":case_summary}]
    })

    decision = result["messages"][-1].content[0]["text"]
    print("decision from manager ----->",decision)
    event = {"type":"manager","message":f"Decision: {decision[:200]}"}
    publish(conversation_id,event)
    AgentLog.objects.create(conversation=conv, event_type="manager", message=f"Decision: {decision[:200]}")
    return decision


def run_risk_agent_langchain(user_id,conversation_id):
    conv = Conversation.objects.get(id=conversation_id)
    event = {"type":"risk","message":f"Starting fraud assessment for user {user_id}"}
    publish(conversation_id,event)
    # log assessment started
    AgentLog.objects.create(conversation=conv, event_type="risk", message=f"Starting fraud assessment for user {user_id}")

    # middleware
    @wrap_tool_call
    def manager_log_tool_calls_middleware(request,handler):
        # before calling the tool
        tool_name = request.tool_call["name"]
        event = {"type":"risk","message":f"Calling {tool_name} to get customer risk profile..."}
        publish(conversation_id,event)
        AgentLog.objects.create(conversation=conv, event_type="risk", message=f"Calling {tool_name} to get customer risk profile...")

        result = handler(request) # tool execution happen here

        # after executing the tools

        return result

    risk_agent = create_agent(
        model =llm,
        system_prompt=RISK_SYSTEM_PROMPT,
        tools =[get_customer_risk_profile],
        middleware=[manager_log_tool_calls_middleware]
    )
    result = risk_agent.invoke({
        "messages" : [{"role":"user","content":"Please assess the fraud risk for user ID {user_id}. User your tool to get their profile and return a verdict"}]
    })
    verdict = result["messages"][-1].content[0]["text"]
    print("verdict----->",verdict)
    event = {"type":"risk","message":f"Verdict: {verdict[:200]}"}
    publish(conversation_id,event)
    AgentLog.objects.create(conversation=conv, event_type="risk", message=f"Verdict: {verdict[:200]}")
    return verdict



    
