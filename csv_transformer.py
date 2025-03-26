import csv
import json
from pathlib import Path
import io

async def transform_for_listmonk(input_path: Path, output_path: Path):
    buffer = io.StringIO()
    
    with open(input_path, mode='r', encoding='utf-8') as infile:
        reader = csv.DictReader(infile)
        fieldnames = ['email', 'name', 'attributes']
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator='\n')
        writer.writeheader()
        
        for row in reader:
            attributes = {key: row[key] for key in row if key not in ['email', 'name']}
            row_data = {
                'email': row['email'],
                'name': row['name'],
                'attributes': json.dumps(attributes, ensure_ascii=False)
            }
            writer.writerow(row_data)

    csv_content = buffer.getvalue().rstrip('\n')
    buffer.close()

    with open(output_path, mode='w', encoding='utf-8') as outfile:
        outfile.write(csv_content)

    return output_path
