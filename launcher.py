import os
import sys
import subprocess

def main():
    # 🛡️ រកទីតាំងបច្ចុប្បន្នរបស់ Launcher
    current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    
    # 🎯 កំណត់ផ្លូវទៅកាន់ file main.py
    main_script = os.path.join(current_dir, "main.py")
    
    if os.path.exists(main_script):
        # 🚀 ហៅបញ្ជាឱ្យ python រត់ file main.py ដោយលាក់ផ្ទាំងខ្មៅ (Console)
        subprocess.Popen([sys.executable, main_script], creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        print(f"Error: Could not find main.py at {main_script}")

if __name__ == "__main__":
    main()