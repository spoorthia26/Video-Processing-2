"""Quick diagnostic script to check video processing status"""
import sqlite3
from pathlib import Path

db_path = Path(__file__).parent / "db" / "video_rag.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("=" * 60)
print("VIDEO PROCESSING STATUS CHECK")
print("=" * 60)

# Check videos
cursor.execute("SELECT COUNT(*) FROM videos")
video_count = cursor.fetchone()[0]
print(f"\nTotal Videos: {video_count}")

# Check processing results by status
cursor.execute("""
    SELECT status, COUNT(*) 
    FROM processing_results 
    GROUP BY status
""")
status_counts = cursor.fetchall()

print("\nProcessing Results by Status:")
for status, count in status_counts:
    print(f"  {status}: {count}")

# Check most recent processing results
cursor.execute("""
    SELECT v.filename, pr.status, pr.speech_model, pr.vision_model, 
           datetime(pr.updated_at) as last_update
    FROM processing_results pr
    JOIN videos v ON v.id = pr.video_id
    ORDER BY pr.updated_at DESC
    LIMIT 5
""")

print("\nMost Recent Processing Results:")
for row in cursor.fetchall():
    filename, status, speech, vision, updated = row
    print(f"  {filename[:40]:<40} | {status:<12} | {speech}/{vision}")
    print(f"    Last updated: {updated}")

# Check for stuck processing jobs
cursor.execute("""
    SELECT v.filename, pr.status, 
           datetime(pr.updated_at) as last_update,
           (julianday('now') - julianday(pr.updated_at)) * 24 as hours_ago
    FROM processing_results pr
    JOIN videos v ON v.id = pr.video_id
    WHERE pr.status = 'processing'
""")

stuck_jobs = cursor.fetchall()
if stuck_jobs:
    print("\n⚠️  STUCK JOBS (status='processing'):")
    for row in stuck_jobs:
        filename, status, updated, hours_ago = row
        print(f"  {filename}")
        print(f"    Stuck for {hours_ago:.1f} hours (since {updated})")
else:
    print("\n✓ No stuck jobs found")

conn.close()

print("\n" + "=" * 60)
print("DIAGNOSIS:")
print("=" * 60)
if stuck_jobs:
    print("⚠️  Videos are stuck in 'processing' status!")
    print("\nPossible causes:")
    print("  1. The Processor Agent is not running")
    print("  2. The Processor Agent crashed during processing")
    print("  3. The video processing failed but status wasn't updated")
    print("\nTo fix:")
    print("  1. Stop all processes (Ctrl+C)")
    print("  2. Run: python main.py")
    print("  3. Check for error messages in the output")
else:
    print("✓ System appears healthy")
