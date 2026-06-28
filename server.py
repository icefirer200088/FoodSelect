#!/usr/bin/env python3
"""FoodSelect server - serves static files + API"""

import http.server
import json
import os
import random
import re

PORT = 8080
DIR = os.path.dirname(os.path.abspath(__file__))
MENU_FILE = os.path.join(DIR, "菜单.md")
SELECTIONS_DIR = os.path.join(DIR, "selections")
os.makedirs(SELECTIONS_DIR, exist_ok=True)

def parse_menu_md(text):
    """Parse 菜单.md into sections -> list of dishes."""
    sections = {}
    current_section = None
    current_cat = None
    dishes = []
    
    for line in text.split('\n'):
        if line.startswith('## '):
            # Save previous section
            if current_section and dishes:
                sections.setdefault(current_section, {})[current_cat or '__main__'] = dishes
                dishes = []
            current_section = line.strip('# ')
            current_cat = None
        elif line.startswith('### '):
            if current_section and dishes:
                sections.setdefault(current_section, {})[current_cat or '__main__'] = dishes
                dishes = []
            current_cat = line.strip('# ')
        elif line.strip().startswith('- '):
            dish = line.strip('- ').strip()
            if dish and not dish.startswith('（') and not dish.startswith('['):
                dishes.append(dish)
    
    if current_section and dishes:
        sections.setdefault(current_section, {})[current_cat or '__main__'] = dishes
    
    return sections

def get_dishes_by_section():
    """Return dict: {section_name: [dishes...]} tracking which section each dish belongs to."""
    if not os.path.exists(MENU_FILE):
        return {}
    with open(MENU_FILE, 'r', encoding='utf-8') as f:
        text = f.read()
    sections = parse_menu_md(text)
    result = {}
    for section, cats in sections.items():
        # Normalize section name: strip parenthetical count
        norm = section.split('(')[0].strip() if '(' in section else section.strip()
        flat = []
        for cat, dishes in cats.items():
            flat.extend(dishes)
        result[norm] = flat
    return result


def get_all_dishes():
    """Return flat list of all dish names from 菜单.md."""
    if not os.path.exists(MENU_FILE):
        return []
    with open(MENU_FILE, 'r', encoding='utf-8') as f:
        text = f.read()
    sections = parse_menu_md(text)
    all_dishes = []
    for section, cats in sections.items():
        for cat, dishes in cats.items():
            all_dishes.extend(dishes)
    return all_dishes


def add_dishes_to_menu(new_dishes):
    """Add new dishes to the 家庭菜 section of 菜单.md. Returns list of actually added dishes."""
    if not new_dishes:
        return []
    with open(MENU_FILE, 'r', encoding='utf-8') as f:
        text = f.read()
    
    # Find the 家庭菜 section
    family_marker = '## 家庭菜'
    idx = text.find(family_marker)
    if idx < 0:
        return []  # No family section found
    
    # Find end of family section (next ## or EOF)
    end_idx = text.find('\n## ', idx + len(family_marker))
    if end_idx < 0:
        end_idx = len(text)
    
    family_block = text[idx:end_idx]
    family_lines = family_block.split('\n')
    
    # Find the placeholder line and actual dishes
    actual = []
    placeholder_line = None
    for i, line in enumerate(family_lines):
        stripped = line.strip()
        if stripped.startswith('- '):
            dish = stripped[2:].strip()
            if dish and not dish.startswith('（') and not dish.startswith('['):
                actual.append(dish)
        elif stripped == '（留空，用户自填）':
            placeholder_line = i
    
    # Filter out dishes already in family section
    to_add = [d for d in new_dishes if d not in actual]
    if not to_add:
        return []
    
    # Add to family block
    new_lines = []
    for d in to_add:
        new_lines.append(f'- {d}')
    
    # If placeholder exists, replace it with first new dish
    added_lines = list(new_lines)
    if placeholder_line is not None:
        family_lines[placeholder_line] = added_lines.pop(0)
    
    # Append remaining
    for line in added_lines:
        family_lines.append(line)
    
    # Update the header count
    header = family_lines[0]
    final_dish_count = len(actual) + len(to_add)
    import re as re2
    header = re2.sub(r'\((\d+)道\)', f'({final_dish_count}道)', header)
    family_lines[0] = header
    
    new_family_block = '\n'.join(family_lines)
    new_text = text[:idx] + new_family_block + text[end_idx:]
    
    with open(MENU_FILE, 'w', encoding='utf-8') as f:
        f.write(new_text)
    
    return to_add
class FoodSelectHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)
    
    def log_message(self, fmt, *args):
        print(f"[FoodSelect] {self.client_address[0]} - {fmt % args}")
    
    def do_GET(self):
        if self.path == '/api/dishes':
            self.send_json({'dishes': get_all_dishes()})
        elif self.path == '/api/menu':
            self.send_file(MENU_FILE, 'text/markdown; charset=utf-8')
        elif self.path.startswith('/api/random'):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            count = int(qs.get('count', [50])[0])

            # Ensure family dishes get priority: include up to 10
            by_section = get_dishes_by_section()
            family_dishes = [d for d in by_section.get('家庭菜', [])
                             if d and not d.startswith('（') and not d.startswith('[')]
            other_dishes = []
            for sname, dishes in by_section.items():
                if sname != '家庭菜':
                    other_dishes.extend(dishes)

            # How many family dishes to include (max 10, min available)
            n_family = min(len(family_dishes), 10)
            # Pick family dishes randomly
            random.shuffle(family_dishes)
            selected = family_dishes[:n_family]

            # Fill the rest from other dishes
            remaining = count - n_family
            if remaining > 0 and other_dishes:
                random.shuffle(other_dishes)
                selected += other_dishes[:min(remaining, len(other_dishes))]

            random.shuffle(selected)
            self.send_json({'dishes': selected, 'total': len(family_dishes) + len(other_dishes), 'family_count': n_family})
        elif self.path.startswith('/api/selections'):
            self.handle_get_selections()
        else:
            super().do_GET()
    
    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len)
        
        if self.path == '/api/menu':
            self.handle_update_menu(body)
        elif self.path == '/api/save_selections':
            self.handle_save_selections(body)
        else:
            self.send_error(404)
    
    def handle_update_menu(self, body):
        data = json.loads(body)
        text = data.get('content', '')
        with open(MENU_FILE, 'w', encoding='utf-8') as f:
            f.write(text)
        self.send_json({'status': 'ok', 'message': '菜单已更新'})

    def handle_save_selections(self, body):
        data = json.loads(body)
        date = data.get('date', '')
        dishes = data.get('dishes', [])
        if not date:
            import datetime
            date = datetime.datetime.now().strftime('%Y-%m-%d')
        filepath = os.path.join(SELECTIONS_DIR, f"{date}.json")
        if len(dishes) == 0:
            # Empty list = delete the saved file
            if os.path.exists(filepath):
                os.remove(filepath)
            self.send_json({'status': 'ok', 'date': date, 'count': 0, 'deleted': True})
        else:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump({'date': date, 'dishes': dishes}, f, ensure_ascii=False, indent=2)
            # Auto-add hand-typed dishes to the menu
            all_menu = set(get_all_dishes())
            new_dishes = [d for d in dishes if d not in all_menu]
            if new_dishes:
                added = add_dishes_to_menu(new_dishes)
            else:
                added = []
            self.send_json({'status': 'ok', 'date': date, 'count': len(dishes), 'added_to_menu': added})
    
    def handle_get_selections(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        date_only = qs.get('date', [None])[0]
        if date_only:
            fp = os.path.join(SELECTIONS_DIR, f"{date_only}.json")
            if os.path.exists(fp):
                with open(fp, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.send_json({'date': date_only, 'dishes': data.get('dishes', [])})
            else:
                self.send_json({'date': date_only, 'dishes': []})
            return
        files = sorted(os.listdir(SELECTIONS_DIR), reverse=True)[:30]
        results = []
        for fname in files:
            if not fname.endswith('.json'):
                continue
            fp = os.path.join(SELECTIONS_DIR, fname)
            with open(fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            results.append({'date': fname.replace('.json',''), 'dishes': data.get('dishes', [])})
        self.send_json({'selections': results})

    
    def send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(data))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)
    
    def send_file(self, path, mime):
        with open(path, 'r', encoding='utf-8') as f:
            data = f.read().encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', len(data))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

if __name__ == '__main__':
    os.chdir(DIR)
    from socketserver import ThreadingTCPServer
    with ThreadingTCPServer(("0.0.0.0", PORT), FoodSelectHandler) as httpd:
        print(f"🍽️ FoodSelect serving at http://0.0.0.0:{PORT}/")
        httpd.serve_forever()
