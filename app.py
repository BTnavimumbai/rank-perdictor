from flask import Flask, jsonify, request
from flask_cors import CORS  # Import CORS

app = Flask(__name__)

# Replace with your actual Hostinger website URL
CORS(app, resources={r"/*": {"origins": "https://btnavimumbai.com"}})

@app.route('/calculate', methods=['POST'])
def calculate():
    # Your existing JEE scraping logic
    data = request.json
    response_url = data.get("url")
    # ... process logic ...
    return jsonify({"status": "success", "marks": 150})

if __name__ == "__main__":
    # Render requires binding to 0.0.0.0 and a dynamic port
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
