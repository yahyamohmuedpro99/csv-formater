import csv
import logging
import asyncio
from pathlib import Path
from google import genai
from typing import List, Dict, Optional
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
import aiofiles
import json
from collections import defaultdict
from tqdm import tqdm

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
    api_key = key_manager.get_next_available_key()
    if not api_key:
        logging.error("No available API keys")
        return None

    print(f"\nProcessing contact: {row}")  # Show input data
    client = genai.Client(api_key=api_key)

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

        response = client.models.generate_content(
                    model="gemini-2.0-flash", contents=prompt
                ).text
        
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
        logging.error(f"Error calling Gemini API: {str(e)}")
        return None

async def process_batch(batch: List[Dict], key_manager: KeyManager) -> List[Dict]:
    """Process a batch of contacts concurrently."""
    tasks = [generate_personalized_email(row, key_manager) for row in batch]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]

async def save_results_async(results: List[Dict], output_file: str):
    """Save results to a CSV file asynchronously."""
    fieldnames = ['email', 'name', 'personalized_email']
    
    # Check if file exists and is empty
    try:
        file_empty = not os.path.exists(output_file) or os.path.getsize(output_file) == 0
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

async def main():
    input_file = Path('data.csv')
    output_file = Path('output_ai_transformed.csv')
    
    if not input_file.exists():
        print(f"Error: Input file '{input_file}' not found!")
        return
    
    print(f"Reading contacts from {input_file}...")
    contacts = await read_csv_file_async(input_file)
    
    if not contacts:
        print("No contacts found in the input file!")
        return
    
    processed, successful = await process_contacts(contacts, output_file)
    
    print(f"\nProcessing complete:")
    print(f"- Total contacts processed: {processed}")
    print(f"- Successful transformations: {successful}")
    print(f"- Failed transformations: {processed - successful}")
    print(f"- Results saved to: {output_file}")

if __name__ == "__main__":
    asyncio.run(main())
