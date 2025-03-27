import csv
import logging
import asyncio
from pathlib import Path
import google.generativeai as genai
from typing import List, Dict, Optional
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import aiofiles
import json
from collections import defaultdict
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# Load environment variables
load_dotenv()

class KeyManager:
    def __init__(self):
        self.api_keys = os.getenv('GEMINI_API_KEYS', '').split(',')
        self.key_usage = defaultdict(int)
        self.last_reset = defaultdict(lambda: datetime.now())
        self.current_key_index = 0
        self.usage_file = 'key_usage.json'
        self.load_usage_state()

    def load_usage_state(self):
        try:
            if os.path.exists(self.usage_file):
                with open(self.usage_file, 'r') as f:
                    data = json.load(f)
                    self.key_usage = defaultdict(int, data['usage'])
                    self.last_reset = defaultdict(
                        lambda: datetime.now(),
                        {k: datetime.fromisoformat(v) for k, v in data['reset_times'].items()}
                    )
        except Exception as e:
            logging.error(f"Error loading key usage state: {e}")

    def save_usage_state(self):
        try:
            with open(self.usage_file, 'w') as f:
                json.dump({
                    'usage': dict(self.key_usage),
                    'reset_times': {k: v.isoformat() for k, v in self.last_reset.items()}
                }, f)
        except Exception as e:
            logging.error(f"Error saving key usage state: {e}")

    def get_next_available_key(self) -> Optional[str]:
        attempts = 0
        while attempts < len(self.api_keys):
            key = self.api_keys[self.current_key_index]
            now = datetime.now()
            
            # Check if 24h have passed since last reset
            if (now - self.last_reset[key]) > timedelta(hours=24):
                self.key_usage[key] = 0
                self.last_reset[key] = now

            # If key hasn't exceeded limit
            if self.key_usage[key] < 1450:
                self.key_usage[key] += 1
                self.save_usage_state()
                return key
            
            # Try next key
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            attempts += 1
        
        return None

async def generate_personalized_email(row: Dict, key_manager: KeyManager) -> Optional[Dict]:
    """Generate a personalized email using Gemini AI based on the contact's information."""
    max_retries = 3
    retry_delay = 1

    for attempt in range(max_retries):
        api_key = key_manager.get_next_available_key()
        if not api_key:
            logging.error("No available API keys")
            return None

        print(f"\nProcessing contact: {row}")
        genai.configure(api_key=api_key)
        client = genai.GenerativeModel('gemini-2.0-flash')

        try:
            prompt = f"""
            Create a personalized email message using the data provided in {row}. The output must be plain text only, formatted exactly as follows:

            [email] === [name] === [personalized email message]

            Guidelines:
            1. Use the actual data provided .
            2. The email message body should:
            - Begin with a compliant and engaging tone that subtly incorporates seduction.
            - Highlight the candidate's relevant experience.
            - Gradually transition into suggesting potential opportunities.
            - Then directly ask for a collaboration with our company and ask him for avalibilty so we can discuss more in a meeting .
            3. The message must be fully completed with no placeholders or template markers (e.g., [Your Name], [Your Position], etc.).
            4. Do not include a subject line, closing sign-offs (such as "Regards," or "Sincerely"), or any extra characters.
            5. The message must begin with "Hello" followed by the candidate's name from , with no additional text or greetings (e.g., do not include "Hello Scott," at the very top).
            6. The final output should strictly use the "===" separator to separate the email address, name, and the personalized email message without any extra formatting.

            Example of expected output format (without the quotation marks):

            scott.ramey@halifax.ca === Scott Ramey === Hello Scott, I noticed your role as Division... [rest of the personalized message]

            Ensure that your response meets these requirements exactly.
            """

            response = client.generate_content(prompt).text
            
            # Clean up any markdown code block markers
            response = response.replace('```text', '').replace('```json', '').replace('```', '')
            fields = response.strip().split('===')
            
            result = {
                'email': fields[0].strip(),
                'name': fields[1].strip(),
                'personalized_email': fields[2].strip()
            }
            
            # Show the generated content
            print(f"\nGenerated email for {result['name']}:")
            print("-" * 50)
            print(f"Email: {result['email']}")
            print(f"Content: {result['personalized_email'][:100]}...")  # Show first 100 chars
            print("-" * 50)
            
            
            return result
                
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "quota" in error_msg.lower():
                # Mark this key as fully used
                key_manager.key_usage[api_key] = 1500
                key_manager.save_usage_state()
                
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logging.warning(f"Rate limit hit, waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                    continue
            
            logging.error(f"Error processing contact {row.get('email', 'unknown')}: {error_msg}")
            return None
    
    return None

async def process_batch(batch: List[Dict], key_manager: KeyManager) -> List[Dict]:
    """Process a batch of contacts concurrently."""
    results = []
    for row in batch:
        try:
            result = await generate_personalized_email(row, key_manager)
            if result:
                results.append(result)
            else:
                logging.warning(f"Skipping contact due to error: {row.get('email', 'unknown')}")
        except Exception as e:
            logging.error(f"Unexpected error processing contact {row.get('email', 'unknown')}: {str(e)}")
            continue
        
        # Add a small delay between processing to avoid overwhelming the API
        await asyncio.sleep(0.5)
    
    return results

def ensure_directories():
    """Create necessary directories if they don't exist."""
    base_dir = Path(__file__).parent
    dirs = [
        base_dir / 'uploads',
        base_dir / 'uploads' / 'processed',
        base_dir / 'uploads' / 'listmonk'
    ]
    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
    return base_dir

async def save_results_async(results: List[Dict], output_file: Path):
    """Save results to a CSV file asynchronously."""
    fieldnames = ['email', 'name', 'personalized_email']
    
    # Create parent directories if they don't exist
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        file_empty = not output_file.exists() or output_file.stat().st_size == 0
    except OSError:
        file_empty = True

    async with aiofiles.open(output_file, 'a', newline='', encoding='utf-8') as file:
        if file_empty:
            print(f"\nCreating new output file: {output_file}")
            header_line = ','.join(fieldnames) + '\n'
            await file.write(header_line)
        
        # Write data rows
        for row in results:
            csv_line = ','.join("\"{0}\"".format(str(row.get(field, '')).replace('"', '""')) for field in fieldnames) + '\n'
            await file.write(csv_line)
            print(f"Saved email for: {row['name']}")

async def process_contacts(contacts: List[Dict], output_file: str, batch_size: int = 5):
    """Process contacts in batches with API key rotation."""
    key_manager = KeyManager()
    total_contacts = len(contacts)
    processed = 0
    successful = 0
    
    print(f"\nStarting to process {total_contacts} contacts...")
    progress_bar = tqdm(total=total_contacts, desc="Processing contacts")
    
    # Process in batches
    for i in range(0, total_contacts, batch_size):
        batch = contacts[i:i + batch_size]
        results = await process_batch(batch, key_manager)
        processed += len(batch)
        
        if results:
            successful += len(results)
            await save_results_async(results, output_file)
        
        progress_bar.update(len(batch))
        # Small delay between batches to prevent rate limiting
        await asyncio.sleep(1)
    
    progress_bar.close()
    return processed, successful

async def read_csv_file_async(file_path: Path) -> List[Dict]:
    """Read the CSV file asynchronously and return list of dictionaries."""
    async with aiofiles.open(file_path, 'r', encoding='utf-8') as file:
        content = await file.read()
        return list(csv.DictReader(content.splitlines()))

def get_file_paths() -> Dict[str, Path]:
    """Get standardized file paths for the application."""
    base_dir = Path(__file__).parent
    return {
        'base': base_dir,
        'uploads': base_dir / 'uploads',
        'processed': base_dir / 'uploads' / 'processed',
        'listmonk': base_dir / 'uploads' / 'listmonk',
        'input': base_dir / 'uploads' / 'data.csv',
        'output': base_dir / 'uploads' / 'processed' / 'output_ai_transformed.csv',
        'subscribers': base_dir / 'uploads' / 'listmonk' / 'subscribers.csv'
    }

def verify_input_file(file_path: Path) -> bool:
    """Verify that the input file exists and is readable."""
    if not file_path.exists():
        logging.error(f"Input file not found: {file_path}")
        return False
    if not file_path.is_file():
        logging.error(f"Path is not a file: {file_path}")
        return False
    return True

async def main():
    paths = get_file_paths()
    ensure_directories()  # Create necessary directories
    
    if not verify_input_file(paths['input']):
        print(f"Error: Input file not found in uploads folder: {paths['input']}")
        return
    
    print(f"Reading contacts from {paths['input']}...")
    contacts = await read_csv_file_async(paths['input'])
    
    if not contacts:
        print("No contacts found in the input file!")
        return
    
    processed, successful = await process_contacts(contacts, paths['output'])
    
    print(f"\nProcessing complete:")
    print(f"- Total contacts processed: {processed}")
    print(f"- Successful transformations: {successful}")
    print(f"- Failed transformations: {processed - successful}")
    print(f"- Results saved to: {paths['output']}")
    print(f"- Listmonk subscribers file: {paths['subscribers']}")

if __name__ == "__main__":
    asyncio.run(main())
