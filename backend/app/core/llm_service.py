import os
from groq import Groq

# Initialize the client globally to reuse the connection pool.
# It will look for the GROQ_API_KEY in your environment variables.
api_key = os.environ.get("GROQ_API_KEY")

def call_groq_lpu(prompt: str) -> str:
    """
    Centralized remote Groq LPU inference client manager.
    Takes a raw string prompt, logs it to the terminal, sends it to Groq, 
    and returns the string response.
    """
    if not api_key:
        # If no API key is found, raise an exception. 
        # This safely triggers the Quick Echo Test fallback in nodes.py!
        raise ValueError("GROQ_API_KEY is not set in the environment.")
        
    client = Groq(api_key=api_key)

    # 📡 CENTRALIZED PROMPT TELEMETRY
    print("\n" + "░"*60)
    print("📤 [LLM PROMPT DISPATCHED]")
    print("-" * 60)
    print(prompt.strip())
    print("░"*60 + "\n")

    try:
        # Using the versatile model with 0 temperature for strict compliance
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="openai/gpt-oss-120b",
            temperature=0, 
        )
        return chat_completion.choices[0].message.content
        
    except Exception as e:
        print(f"🚨 [LLM Service Error]: {e}")
        raise e