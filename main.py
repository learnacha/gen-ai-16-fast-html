from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sqlite3
import uuid
import os
import uvicorn
from PIL import Image
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from typing import List, Optional
from threading import Thread

# Load environment variables from .env file
load_dotenv()
API_KEY = os.getenv("HUGGINGFACE_API_KEY") + "y"

# Initialize the InferenceClient with your API key
client = InferenceClient(token=API_KEY) if API_KEY else None

# Ensure directories exist
os.makedirs("data", exist_ok=True)
os.makedirs("data/gens", exist_ok=True)
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Create database connection
conn = sqlite3.connect('data/gens.db')
cursor = conn.cursor()

# Create table if it doesn't exist
cursor.execute('''
CREATE TABLE IF NOT EXISTS gens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    folder TEXT NOT NULL
)
''')
conn.commit()

# Create FastAPI app
app = FastAPI()

# Templates directory for HTML templates
templates = Jinja2Templates(directory="templates")

# Serve static files and generated images
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/data", StaticFiles(directory="data"), name="data")

# Create HTML template file
with open("templates/index.html", "w") as f:
    f.write('''
<!DOCTYPE html>
<html>
<head>
    <title>Image Generation Demo</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@1/css/pico.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/flexboxgrid/6.3.1/flexboxgrid.min.css">
    <script src="https://unpkg.com/htmx.org@1.9.6"></script>
    <style>
        .card { margin-bottom: 1rem; }
        .card img { width: 100%; height: auto; }
    </style>
</head>
<body>
    <main class="container">
        <h1>Magic Image Generation</h1>
        
        <form hx-post="/generate" hx-target="#gen-list" hx-swap="afterbegin">
            <div class="grid">
                <input id="new-prompt" name="prompt" placeholder="Enter a prompt" required>
                <button type="submit">Generate</button>
            </div>
        </form>
        
        <div id="gen-list" class="row">
            {% for gen in generations %}
            <div id="gen-{{ gen.id }}" class="box col-xs-12 col-sm-6 col-md-4 col-lg-3">
                {% if gen.image_exists %}
                <div class="card">
                    <img src="/data/gens/{{ gen.folder_name }}/{{ gen.id }}.png" alt="Generated image" class="card-img-top">
                    <div class="card-body">
                        <p class="card-text"><b>Prompt: </b>{{ gen.prompt }}</p>
                    </div>
                </div>
                {% else %}
                <div hx-get="/gens/{{ gen.id }}" hx-trigger="every 2s" hx-swap="outerHTML">
                    Generating gen {{ gen.id }} with prompt {{ gen.prompt }}
                </div>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </main>
</body>
</html>
''')

# Models
class Generation(BaseModel):
    id: int
    prompt: str
    folder: str
    image_exists: bool = False
    folder_name: str = ""
    
# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # Get recent generations from the database
    cursor.execute("SELECT id, prompt, folder FROM gens ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    
    generations = []
    for row in rows:
        gen_id, prompt, folder = row
        folder_name = os.path.basename(folder)
        image_path = f"{folder}/{gen_id}.png"
        image_exists = os.path.exists(image_path)
        
        generations.append({
            "id": gen_id,
            "prompt": prompt,
            "folder": folder,
            "folder_name": folder_name,
            "image_exists": image_exists
        })
    
    return templates.TemplateResponse("index.html", {"request": request, "generations": generations})

@app.get("/gens/{gen_id}")
async def get_generation(gen_id: int, request: Request):
    # Get generation from database
    cursor.execute("SELECT id, prompt, folder FROM gens WHERE id = ?", (gen_id,))
    row = cursor.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Generation not found")
    
    gen_id, prompt, folder = row
    folder_name = os.path.basename(folder)
    image_path = f"{folder}/{gen_id}.png"
    image_exists = os.path.exists(image_path)
    
    # If image exists, return HTML with image
    if image_exists:
        return f'''
        <div id="gen-{gen_id}" class="box col-xs-12 col-sm-6 col-md-4 col-lg-3">
            <div class="card">
                <img src="/data/gens/{folder_name}/{gen_id}.png" alt="Generated image" class="card-img-top">
                <div class="card-body">
                    <p class="card-text"><b>Prompt: </b>{prompt}</p>
                </div>
            </div>
        </div>
        '''
    else:
        # Otherwise, return HTML that will continue polling
        return f'''
        <div id="gen-{gen_id}" class="box col-xs-12 col-sm-6 col-md-4 col-lg-3" hx-get="/gens/{gen_id}" hx-trigger="every 2s" hx-swap="outerHTML">
            Generating gen {gen_id} with prompt {prompt}
        </div>
        '''

@app.post("/generate")
async def generate_image(prompt: str = Form(...)):
    # Create a unique folder for the image
    folder_name = str(uuid.uuid4())
    folder = f"data/gens/{folder_name}"
    os.makedirs(folder, exist_ok=True)
    
    # Insert into database
    cursor.execute("INSERT INTO gens (prompt, folder) VALUES (?, ?)", (prompt, folder))
    conn.commit()
    gen_id = cursor.lastrowid
    
    # Start the generation in a separate thread
    Thread(target=generate_and_save, args=(prompt, gen_id, folder)).start()
    
    # Return HTML for pending generation that will poll for updates
    return f'''
    <div id="gen-{gen_id}" class="box col-xs-12 col-sm-6 col-md-4 col-lg-3" hx-get="/gens/{gen_id}" hx-trigger="every 2s" hx-swap="outerHTML">
        Generating gen {gen_id} with prompt {prompt}
    </div>
    <script>
        // Clear input field
        document.getElementById('new-prompt').value = '';
    </script>
    '''

# Generate an image and save it to the folder
def generate_and_save(prompt, id, folder):
    if not client:
        print(f"Skipping generation for ID {id}: InferenceClient not available.")
        return False

    save_path = f"{folder}/{id}.png"
    try:
        print(f"[{id}] Starting image generation via InferenceClient for prompt: '{prompt}'...")
        image = client.text_to_image(
            prompt,
            model="black-forest-labs/FLUX.1-dev"  # Use the new model
        )
        # Save the image (image is already a PIL Image object)
        image.save(save_path)
        print(f"[{id}] Image generated and saved successfully to {save_path}")
        return True
    except Exception as e:
        print(f"[{id}] Failed to generate or save image for prompt '{prompt}': {e}")
        return False

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", default=8000)))
