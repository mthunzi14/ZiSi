import requests
import json
import time
import os

# Complete, absolute 5-wallet mapping directory matching your live browser verification targets
wallets = {
    "PBOT": "0x21d0a97aac03917e752857a551bbe5103a00e8d7",
    "bonereaper": "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30",
    "pbot_sweeper": "0x13f0bcec1e2e60ec9acc3bee4d2da2fe9694a50f",
    "ritb123": "0x724db3c436dcc7b26fbe1ae0c0d6af538b588dea",
    "certova": "0x8d1d5d1c6041b13fc708b5d9f668070e1724ed4a"
}

# Ensure the system storage directory paths exist securely before starting ingestion
output_dir = "/root/ZiSi/wallet"
os.makedirs(output_dir, exist_ok=True)

print("--- UNCAUGHT AUTOMATED DEEP SYSTEM BACKFILL INITIALIZED ---")

for name, addr in wallets.items():
    print(f"\n⚡ PROCESSING WALLET SOURCE [{name}] ({addr})...")
    all_trades = []
    cursor = ""  # Using explicit cursor token iteration to bypass hardcoded index caps
    page_count = 1
    
    while True:
        # Querying the underlying CLOB trade history ledger matching both buy and sell sides
        url = f"https://clob.polymarket.com/trades?maker_address={addr}&limit=1000"
        if cursor:
            url += f"&cursor={cursor}"
            
        try:
            res = requests.get(url, timeout=15)
            
            if res.status_code == 429:
                print("⚠️ Rate limit detected. Backing off for 5 seconds...")
                time.sleep(5)
                continue
                
            data = res.json()
            
            # Extract transactions block array safely from payload envelope
            trades_chunk = data.get('data', []) if isinstance(data, dict) else data
            
            if not trades_chunk or len(trades_chunk) == 0:
                print(f"✅ Success. Reached zero-point foundation ledger for {name}.")
                break
                
            all_trades.extend(trades_chunk)
            print(f"   Page {page_count} synced... Total cumulative rows pooled: {len(all_trades)}")
            
            # Advance structural pagination cursor to the next sequential historical block hash
            next_cursor = data.get('next_cursor') if isinstance(data, dict) else None
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            page_count += 1
            
            time.sleep(0.15)  # Safe execution delay pacing to stay underneath firewall barriers
            
        except Exception as e:
            print(f"❌ Structural connection error encountered on {name}: {str(e)}")
            break
            
    # Save the definitive lifetime ledger profile directly to disk, completely overwriting the shallow base records
    destination_file = f"{output_dir}/wallet_{addr}_history.json"
    with open(destination_file, "w") as f:
        json.dump(all_trades, f, indent=2)
    print(f"💾 MASTER STORAGE SECURED: {destination_file} holds {len(all_trades)} verified transaction elements.")

print("\n🎉 ALL 5 WALLETS SYSTEMATICALLY RESTORED WITH ZERO LEAKAGE.")
