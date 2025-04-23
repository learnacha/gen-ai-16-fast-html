from fastcore.parallel import threaded
from fasthtml.common import *
import uuid, os, uvicorn
from PIL import Image # PIL is used by InferenceClient result and for saving
# import io # No longer needed for BytesIO
# import requests # No longer needed
from dotenv import load_dotenv
from huggingface_hub import InferenceClient # Import the InferenceClient

# Load environment variables from .env file
load_dotenv()
API_KEY = os.getenv("HUGGINGFACE_API_KEY") + "y"
# API_URL = "https://api-inference.huggingface.co/models/alvdansen/littletinies" # Old API URL - Removed
# headers = {"Authorization": f"Bearer {API_KEY}"} # Old headers - Removed

# --- New: Initialize the InferenceClient ---
if not API_KEY:
    print("Error: Hugging Face API token not found! Please set HUGGINGFACE_API_KEY in your .env file.")
    # You might want to exit or handle this more gracefully depending on your needs
    # For now, we'll let it proceed, but generation will fail.
    client = None
else:
    try:
        client = InferenceClient(token=API_KEY)
        print("Hugging Face InferenceClient initialized successfully.")
    except Exception as e:
        print(f"Error initializing Huggin Face InferenceClient: {e}")
        client = None
# --- End New ---

# --- Removed old query function ---
# def query(payload):
#     response = requests.post(API_URL, headers=headers, json=payload)
#     return response.content
# --- End Removed ---

# gens database for storing generated image details
tables = database('data/gens.db').t
gens = tables.gens
if not gens in tables:
    gens.create(prompt=str, id=int, folder=str, pk='id')
Generation = gens.dataclass()

# Flexbox CSS (http://flexboxgrid.com/)
gridlink = Link(rel="stylesheet", href="https://cdnjs.cloudflare.com/ajax/libs/flexboxgrid/6.3.1/flexboxgrid.min.css", type="text/css")

# Our FastHTML app
app = FastHTML(hdrs=(picolink, gridlink))

# Main page
@app.get("/")
def home():
    inp = Input(id="new-prompt", name="prompt", placeholder="Enter a prompt")
    # Add a check for the API key status
    if not API_KEY or client is None:
        add_section = Div(
            P("⚠️ Hugging Face API Key not configured correctly. Please set HUGGINGFACE_API_KEY in your .env file.", style="color: red;"),
            Form(Group(inp, Button("Generate", disabled=True))) # Disable button if no key/client
        )
    else:
         add_section = Form(Group(inp, Button("Generate")), hx_post="/", target_id='gen-list', hx_swap="afterbegin")

    # Fetch existing generations
    try:
        gen_records = gens(limit=10)
    except Exception as e:
        print(f"Database error fetching generations: {e}") # Log DB errors
        gen_records = []

    gen_containers = [generation_preview(g) for g in gen_records] # Start with last 10
    gen_list = Div(*reversed(gen_containers), id='gen-list', cls="row") # flexbox container: class = row
    return Title('Image Generation Demo'), Main(H1('Magic Image Generation'), add_section, gen_list, cls='container')

# Show the image (if available) and prompt for a generation
def generation_preview(g):
    grid_cls = "box col-xs-12 col-sm-6 col-md-4 col-lg-3"
    image_path = f"{g.folder}/{g.id}.png"
    image_preview_content = []

    if os.path.exists(image_path):
        # Check if the image file is valid (e.g., not 0 bytes)
        try:
            if os.path.getsize(image_path) > 0:
                 # Attempt to open to ensure it's a valid image file
                 with Image.open(image_path) as img:
                     img.verify() # Verify closes the file
                 image_preview_content.append(Img(src=image_path, alt="Generated image", cls="card-img-top"))
            else:
                print(f"Warning: Image file is empty for gen {g.id} at {image_path}")
                image_preview_content.append(P("⚠️ Error: Image file is empty.", cls="card-text", style="color: orange;"))
        except (FileNotFoundError, OSError, Image.UnidentifiedImageError, ValueError) as e:
             print(f"Error loading/verifying image for gen {g.id} at {image_path}: {e}")
             image_preview_content.append(P(f"⚠️ Error loading image: {e}", cls="card-text", style="color: orange;"))

        image_preview_content.append(Div(P(B("Prompt: "), g.prompt, cls="card-text"), cls="card-body"))
        # Card structure for existing image
        return Div(Card(*image_preview_content), id=f'gen-{g.id}', cls=grid_cls)
    else:
        # Structure for pending generation (polling)
        return Div(
            Card(
                Div(
                    P(f"⏳ Generating image for prompt:", cls="card-text"),
                    P(B(g.prompt), cls="card-text"),
                    # Optional: Add a spinner or loading indicator here
                    # E.g., Img(src="/static/spinner.gif", alt="Loading...")
                    cls="card-body"
                )
            ),
            id=f'gen-{g.id}', hx_get=f"/gens/{g.id}",
            hx_trigger="every 3s", hx_swap="outerHTML", # Increased polling interval slightly
            cls=grid_cls
        )


# A pending preview keeps polling this route until we return the image preview
@app.get("/gens/{id}")
def preview(id:int):
    try:
        g = gens.get(id)
        return generation_preview(g)
    except Exception as e:
        # Handle case where generation ID might not exist (e.g., DB issue)
        print(f"Error fetching generation {id} for preview: {e}")
        # Return an error message placeholder
        return Div(P(f"Error finding generation {id}.", style="color: red;"), id=f'gen-{id}', cls="box col-xs-12 col-sm-6 col-md-4 col-lg-3")

# For images, CSS, etc.
@app.get("/{fname:path}.{ext:static}")
def static(fname:str, ext:str):
    file_path = f'{fname}.{ext}'
    # Basic security check: Ensure the path doesn't try to escape the intended directory
    # This is a very basic check, consider more robust validation if needed.
    if ".." in file_path or not os.path.exists(file_path) or not os.path.isfile(file_path):
        # Return a 404 Not Found response if the file doesn't exist or path is suspicious
        # This requires returning an appropriate HTTP response, which FileResponse doesn't directly handle
        # For simplicity here, we might just log and let it potentially fail,
        # but a real app should return a proper 404 status code.
        print(f"Static file access denied or file not found: {file_path}")
        # Placeholder: Return an empty response or raise an HTTP exception if using a framework that supports it easily
        return P("") # Or handle as appropriate for FastHTML/Starlette error handling

    # Check if the requested path is within an allowed directory (e.g., 'data/gens' or a dedicated 'static' folder)
    # Example: Allow access only within 'data/gens'
    allowed_base = os.path.abspath("data/gens")
    requested_path_abs = os.path.abspath(file_path)
    if not requested_path_abs.startswith(allowed_base):
         print(f"Static file access denied (outside allowed directory): {file_path}")
         return P("") # Or handle as appropriate

    return FileResponse(file_path)


# Generation route
@app.post("/")
def post(prompt:str):
    # Check again if client is available before proceeding
    if not client:
        # This case should ideally be prevented by disabling the button,
        # but double-check here. You might return an error message component.
        return P("Error: Image generation service not available.", style="color: red;")

    folder = f"data/gens/{str(uuid.uuid4())}"
    os.makedirs(folder, exist_ok=True)
    g = gens.insert(Generation(prompt=prompt, folder=folder))
    print(f"Created generation record ID {g.id} in folder {g.folder} for prompt: '{prompt}'")
    generate_and_save(g.prompt, g.id, g.folder) # Call the modified function
    clear_input = Input(id="new-prompt", name="prompt", placeholder="Enter a prompt", hx_swap_oob='true', value="") # Clear input value
    return generation_preview(g), clear_input # Return the initial "pending" preview

# Generate an image and save it to the folder (in a separate thread)
@threaded
def generate_and_save(prompt, id, folder):
    if not client:
        print(f"Skipping generation for ID {id}: InferenceClient not available.")
        return False

    save_path = f"{folder}/{id}.png"
    try:
        print(f"[{id}] Starting image generation via InferenceClient for prompt: '{prompt}'...")
        image = client.text_to_image(
            prompt,
            model="black-forest-labs/FLUX.1-dev" # Use the desired model
        )
        # The result 'image' is already a PIL Image object
        image.save(save_path)
        print(f"[{id}] Image generated and saved successfully to {save_path}")
        return True
    except Exception as e:
        print(f"[{id}] Failed to generate or save image for prompt '{prompt}': {e}")
        # Optionally create an empty file or error placeholder image
        # Or just leave the file missing, the preview logic will handle it.
        # Example: Create an empty file to prevent repeated generation attempts if desired
        # with open(save_path, 'w') as f: pass
        return False

if __name__ == '__main__':
    # Ensure the base data directory exists
    os.makedirs("data/gens", exist_ok=True)
    # Start the server
    uvicorn.run("main:app", host='0.0.0.0', port=int(os.getenv("PORT", default=8000)))
