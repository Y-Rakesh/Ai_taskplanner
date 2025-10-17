import os
import json
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from groq import Groq
from bson import ObjectId
from pymongo import MongoClient

# --- SETUP AND CONFIGURATION ---

load_dotenv()
app = Flask(__name__, static_folder=".", static_url_path="/")

CORS(app)
# --- MONGODB CONFIGURATION ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "planner_db"

try:
    mongo_client = MongoClient(MONGO_URI,serverSelectionTimeoutMS=5000)
    db = mongo_client[DB_NAME]
    goals_collection = db["goals"]
    tasks_collection = db["tasks"]
    print("MongoDB connected successfully.")
except Exception as e:
    print(f" MongoDB connection failed: {e}")

# --- FRONTEND / STATIC PATH ---
#FRONTEND_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend"))

# --- GROQ CLIENT SETUP ---
try:
    groq_api_key = os.getenv("GROQ_API_KEY")
    if not groq_api_key:
        print(" Warning: GROQ_API_KEY not found in .env. Using fallback key.")
        
    client = Groq(api_key=groq_api_key)
    print(" Groq client initialized.")
except Exception as e:
    print(f" Error initializing Groq client: {e}")
    client = None


# --- LLM CALL (Groq) ---
def generate_plan_with_groq(goal_text):
    if not client:
        raise ConnectionError("Groq client not initialized.")
    system_prompt = """
    You are a world-class project manager. Break down a user's goal into 3–7 actionable tasks.
    Each task must have `task_description`, `dependencies`, and `deadline`.
    Return valid JSON: {"tasks": [...]}
    """
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"My goal: \"{goal_text}\""}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        return json.loads(chat_completion.choices[0].message.content)
    except Exception as e:
        print(f"Groq API error: {e}")
        return None


# --- LOCAL FALLBACK PLAN GENERATOR ---
def generate_plan_locally(goal_text):
    import re
    text = goal_text.lower()
    tasks = [
        {"task_description": "Clarify and break down the goal into actionable steps.", "dependencies": "None", "deadline": "Today"}
    ]
    match = re.search(r"(\d+)\s*hour", text)
    if match:
        tasks.append({
            "task_description": f"Work for {match.group(1)} hour(s).",
            "dependencies": "Clarify and break down the goal",
            "deadline": "Today"
        })
    else:
        tasks.append({
            "task_description": "Work on the project (time-boxed session).",
            "dependencies": "Clarify and break down the goal",
            "deadline": "Today"
        })
    if any(w in text for w in ["eat", "lunch", "food", "dinner", "breakfast"]):
        tasks.append({"task_description": "Take meal breaks.", "dependencies": "None", "deadline": "Today"})
    if "sleep" in text:
        tasks.append({"task_description": "Sleep adequately.", "dependencies": "Finish work", "deadline": "Tonight"})
    if len(tasks) < 3:
        tasks.append({"task_description": "Review and adjust the plan.", "dependencies": "Complete initial tasks", "deadline": "Today"})
    return {"tasks": tasks}


# --- API: GENERATE PLAN ---

@app.route("/")
def serve_frontend():
    return send_from_directory(".", "index.html")
@app.route("/api/generate-plan", methods=["POST"])
def generate_plan_endpoint():
    data = request.json
    if not data or "goal" not in data:
        return jsonify({"error": "JSON must contain 'goal'"}), 400

    goal_text = data["goal"].strip()
    if not goal_text:
        return jsonify({"error": "Goal cannot be empty"}), 400

    print(f"Received Goal: {goal_text}")

    plan_data = generate_plan_with_groq(goal_text)
    used_fallback = False
    if not plan_data or "tasks" not in plan_data:
        plan_data = generate_plan_locally(goal_text)
        used_fallback = True

    tasks = plan_data["tasks"]

    # Insert into MongoDB
    goal_doc = {
        "goal_text": goal_text,
        "created_at": datetime.utcnow()
    }
    goal_id = goals_collection.insert_one(goal_doc).inserted_id

    for task in tasks:
        deps = task.get("dependencies")
        if isinstance(deps, list):
            deps = json.dumps(deps)
        task_doc = {
            "goal_id": goal_id,
            "task_description": task.get("task_description"),
            "dependencies": deps,
            "deadline": task.get("deadline")
        }
        tasks_collection.insert_one(task_doc)

    print(f" Saved goal {goal_id} with {len(tasks)} tasks.")

    response = {
        "goal_id": str(goal_id),
        "goal_text": goal_text,
        "tasks": tasks,
        "used_fallback": used_fallback
    }
    return jsonify(response), 200


# --- API: GET ALL GOALS ---
@app.route("/api/get-all-goals", methods=["GET"])
def get_all_goals():
    try:
        all_goals = []
        for goal in goals_collection.find().sort("created_at", -1):
            goal_id = goal["_id"]
            goal_tasks = list(tasks_collection.find({"goal_id": goal_id}))
            formatted_tasks = []
            for t in goal_tasks:
                deps = t.get("dependencies")
                try:
                    deps = json.loads(deps)
                    if isinstance(deps, list):
                        deps = ", ".join(deps)
                except:
                    pass
                formatted_tasks.append({
                    "task_description": t.get("task_description"),
                    "dependencies": deps,
                    "deadline": t.get("deadline")
                })
            all_goals.append({
                "goal_id": str(goal_id),
                "goal_text": goal.get("goal_text"),
                "created_at": goal.get("created_at").strftime("%Y-%m-%d %H:%M:%S"),
                "tasks": formatted_tasks
            })
        return jsonify(all_goals), 200
    except Exception as e:
        print(f"Error fetching goals: {e}")
        return jsonify({"error": "Failed to fetch goals"}), 500


# --- HEALTH CHECK ---
@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"}), 200




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
