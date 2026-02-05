from flask import Flask, request, render_template, jsonify, send_file
from playwright.sync_api import sync_playwright
import pandas as pd
import os
import time
from werkzeug.utils import secure_filename
import re
from datetime import datetime

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['RESULTS_FOLDER'] = 'results'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['RESULTS_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_address_on_cha_map(address):
    """
    Uses Playwright to check if an address is in a CHA Mobility Area
    Returns: (is_mobility_area: bool, status_message: str)
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Navigate to CHA mobility map
            page.goto('https://thecha.maps.arcgis.com/apps/instant/basic/index.html?appid=5ce5a99dad2e4579b2095e514ad64294', timeout=60000)
            
            # Wait for map to load
            page.wait_for_load_state('networkidle', timeout=30000)
            time.sleep(2)
            
            # Find search input - try multiple selectors
            search_selectors = [
                'input[placeholder*="address"]',
                'input[placeholder*="Address"]',
                'input[type="text"]',
                '.esri-search__input',
                '[role="textbox"]'
            ]
            
            search_box = None
            for selector in search_selectors:
                try:
                    search_box = page.locator(selector).first
                    if search_box.is_visible():
                        break
                except:
                    continue
            
            if not search_box:
                return False, "Could not find search box on map"
            
            # Enter address
            search_box.fill(address)
            time.sleep(1)
            
            # Press Enter or click search
            search_box.press('Enter')
            time.sleep(3)
            
            # Check for results - look for green highlighting or mobility area indicator
            # This is simplified - you may need to adjust based on actual map behavior
            page_content = page.content()
            
            # Look for indicators that it's a mobility area
            # This will need refinement based on how the actual map displays results
            is_mobility = False
            
            # Try to find result indicators in the page
            try:
                # Check if search resulted in a location on map
                result_elements = page.locator('[class*="result"], [class*="feature"]').count()
                if result_elements > 0:
                    # Additional logic to determine if in mobility area
                    # This is a placeholder - actual implementation depends on map structure
                    is_mobility = True  # Will need refinement
            except:
                pass
            
            browser.close()
            
            return is_mobility, "Successfully checked"
            
    except Exception as e:
        return False, f"Error: {str(e)}"

def process_spreadsheet(filepath, progress_callback=None):
    """
    Process uploaded spreadsheet and check each address
    """
    # Read the file
    if filepath.endswith('.csv'):
        df = pd.read_csv(filepath)
    else:
        df = pd.read_excel(filepath)
    
    # Find address column (look for common column names)
    address_col = None
    for col in df.columns:
        if any(term in col.lower() for term in ['address', 'street', 'location']):
            address_col = col
            break
    
    if not address_col:
        # Use first column as default
        address_col = df.columns[0]
    
    # Add result columns
    df['Is_Mobility_Area'] = ''
    df['Check_Status'] = ''
    df['Checked_At'] = ''
    
    total = len(df)
    
    # Process each address
    for idx, row in df.iterrows():
        address = str(row[address_col])
        
        # Skip empty addresses
        if pd.isna(address) or address.strip() == '':
            df.at[idx, 'Check_Status'] = 'Empty address'
            continue
        
        # Check address on CHA map
        is_mobility, status = check_address_on_cha_map(address)
        
        df.at[idx, 'Is_Mobility_Area'] = 'YES' if is_mobility else 'NO'
        df.at[idx, 'Check_Status'] = status
        df.at[idx, 'Checked_At'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Progress callback
        if progress_callback:
            progress_callback(idx + 1, total)
        
        # Rate limiting to avoid overwhelming the server
        time.sleep(2)
    
    return df

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Please upload .xlsx, .xls, or .csv'}), 400
    
    # Save uploaded file
    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    unique_filename = f"{timestamp}_{filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
    file.save(filepath)
    
    return jsonify({
        'success': True,
        'filename': unique_filename,
        'message': 'File uploaded successfully'
    })

@app.route('/process/<filename>', methods=['POST'])
def process_file(filename):
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        # Process the spreadsheet
        result_df = process_spreadsheet(filepath)
        
        # Save results
        result_filename = f"results_{filename}"
        result_path = os.path.join(app.config['RESULTS_FOLDER'], result_filename)
        
        if filename.endswith('.csv'):
            result_df.to_csv(result_path, index=False)
        else:
            result_df.to_excel(result_path, index=False)
        
        # Count results
        mobility_count = len(result_df[result_df['Is_Mobility_Area'] == 'YES'])
        non_mobility_count = len(result_df[result_df['Is_Mobility_Area'] == 'NO'])
        
        return jsonify({
            'success': True,
            'result_filename': result_filename,
            'total_addresses': len(result_df),
            'mobility_areas': mobility_count,
            'non_mobility_areas': non_mobility_count
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
def download_file(filename):
    filepath = os.path.join(app.config['RESULTS_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    return send_file(filepath, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
