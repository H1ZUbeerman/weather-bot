from openai import OpenAI
import os


def get_ai_client():
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        return None

    return OpenAI(api_key=api_key)


def get_ai_summary(prompt):
    try:
        client = get_ai_client()

        if client is None:
            return "AI summary недоступен: отсутствует OPENAI_API_KEY"

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты AI weather assistant. "
                        "Пиши кратко и полезно. "
                        "Давай рекомендации человеку: одежда, зонт, ветер, надежность прогноза."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=0.4,
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"AI недоступен: {e}"