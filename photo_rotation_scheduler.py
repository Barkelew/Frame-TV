import os
import random
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
from pathlib import Path
import logging
import heapq

try:
    from PIL import Image
    EXIF_AVAILABLE = True
except ImportError:
    EXIF_AVAILABLE = False

PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff')
LOG_FILE = "viewed_photos.json"
CACHE_FILE = "photo_dates.json"

class PhotoScheduler:
    def __init__(self):
        self.setup_logging()
        self.setup_gui()
        self.viewed_photos = self.load_viewed_photos()
        self.date_cache = self.load_date_cache()
        self.cache_dirty = False
        self.viewed_photos_lock = threading.Lock()
        self.operation_lock = threading.Lock()
        self.operation_cancelled = threading.Event()
        self.current_thread = None
        
        if not EXIF_AVAILABLE:
            self.logger.info("EXIF disabled (PIL not available - install: pip install Pillow)")
        
        self.update_next_switch()
        self.periodic_update()
        
    def setup_logging(self):
        class NoMillisecondsFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                return datetime.fromtimestamp(record.created).strftime('%m-%d %H:%M:%S')
        
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s',
                          handlers=[logging.StreamHandler()])
        formatter = NoMillisecondsFormatter(fmt='%(asctime)s - %(message)s')
        for handler in logging.getLogger().handlers:
            handler.setFormatter(formatter)
        self.logger = logging.getLogger(__name__)
    
    def load_date_cache(self):
        try:
            if Path(CACHE_FILE).exists():
                with open(CACHE_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading date cache: {e}")
        return {}
    
    def save_date_cache(self):
        if not self.cache_dirty:
            return
        try:
            with open(CACHE_FILE, 'w') as f:
                json.dump(self.date_cache, f)
            self.cache_dirty = False
        except Exception as e:
            self.logger.error(f"Error saving date cache: {e}")
        
    def get_photo_date(self, photo_path):
        """Get photo date with caching"""
        filename = photo_path.name
        
        # Check cache first
        if filename in self.date_cache:
            return self.date_cache[filename]
        
        # Calculate date using fallback hierarchy
        date = self._calculate_photo_date(photo_path)
        self.date_cache[filename] = date
        self.cache_dirty = True
        return date
    
    def _calculate_photo_date(self, photo_path):
        """Calculate photo date using fallback hierarchy"""
        try:
            if EXIF_AVAILABLE:
                try:
                    with Image.open(photo_path) as img:
                        exif = img.getexif()
                        if exif:
                            for tag in [36867, 36868, 306]:  # DateTimeOriginal, Digitized, DateTime
                                if tag in exif and exif[tag]:
                                    return datetime.strptime(exif[tag], '%Y:%m:%d %H:%M:%S').timestamp()
                except Exception:
                    pass
            
            stat = photo_path.stat()
            if hasattr(stat, 'st_birthtime'):
                return stat.st_birthtime
            elif os.name == 'nt':
                return stat.st_ctime
            return stat.st_mtime
        except Exception as e:
            self.logger.warning(f"Error getting date for {photo_path}: {e}")
            return time.time()
    
    def setup_gui(self):
        self.root = tk.Tk()
        self.root.title("Photo Rotation Scheduler")
        self.root.geometry("600x350")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        default_library = Path("/media/adam/DRPHOTOUSB")
        self.library_path = tk.StringVar(value=str(default_library))
        self.library_path.trace_add("write", self.update_gallery_path)
        self.gallery_path_display = tk.StringVar(value="")
        self.photo_count = tk.StringVar(value="50")
        self.switches_per_day = tk.StringVar(value="1")
        self.main_time = tk.StringVar(value="21:15")
        self.selection_mode = tk.StringVar(value="Random")
        self.status = tk.StringVar(value="Ready")
        self.next_switch = tk.StringVar(value="Calculating...")
        
        self.update_gallery_path()
        self.create_ui()
        
    def create_ui(self):
        settings = ttk.LabelFrame(self.root, text="Settings", padding=10)
        settings.pack(fill="x", padx=10, pady=5)
        
        self.create_path_row(settings, "Library:", self.library_path, 0)
        
        ttk.Label(settings, text="Gallery:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(settings, textvariable=self.gallery_path_display, width=45, state="readonly").grid(row=1, column=1, sticky="w", padx=5)
        
        photo_frame = ttk.Frame(settings)
        photo_frame.grid(row=2, column=0, columnspan=2, pady=5)
        ttk.Label(photo_frame, text="Photos:").pack(side="left")
        ttk.Entry(photo_frame, textvariable=self.photo_count, width=8).pack(side="left", padx=5)
        ttk.Label(photo_frame, text="Mode:").pack(side="left", padx=(20,0))
        mode_combo = ttk.Combobox(photo_frame, textvariable=self.selection_mode, width=10, state="readonly")
        mode_combo['values'] = ("Random", "Newest", "Oldest")
        mode_combo.pack(side="left", padx=5)
        
        schedule_frame = ttk.Frame(settings)
        schedule_frame.grid(row=3, column=0, columnspan=2, pady=5)
        ttk.Label(schedule_frame, text="Main Time:").pack(side="left")
        ttk.Entry(schedule_frame, textvariable=self.main_time, width=8).pack(side="left", padx=5)
        ttk.Label(schedule_frame, text="Switches/Day:").pack(side="left", padx=(20,0))
        ttk.Entry(schedule_frame, textvariable=self.switches_per_day, width=8).pack(side="left", padx=5)
        ttk.Button(schedule_frame, text="Update Settings", command=self.update_settings).pack(side="left", padx=(20,0))
        
        status_frame = ttk.LabelFrame(self.root, text="Status", padding=10)
        status_frame.pack(fill="x", padx=10, pady=5)
        ttk.Label(status_frame, textvariable=self.status).pack(anchor="w")
        ttk.Label(status_frame, text="Next Switch:").pack(side="left")
        ttk.Label(status_frame, textvariable=self.next_switch, font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=10)
        self.progress = ttk.Progressbar(status_frame, mode='indeterminate')
        self.progress.pack(fill="x", pady=5)
        
        controls = ttk.Frame(self.root)
        controls.pack(pady=10)
        self.switch_btn = ttk.Button(controls, text="Switch Photos Now", command=self.switch_photos_async)
        self.switch_btn.pack(side="left", padx=5)
        self.clear_btn = ttk.Button(controls, text="Reset Gallery folder", command=self.clear_gallery_async)
        self.clear_btn.pack(side="left", padx=5)
        self.reset_btn = ttk.Button(controls, text="Reset View History", command=self.reset_history)
        self.reset_btn.pack(side="left", padx=5)
        
    def create_path_row(self, parent, label, variable, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=1, sticky="ew", padx=5)
        ttk.Entry(frame, textvariable=variable, width=40).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="...", width=3, command=lambda: self.browse_path(variable)).pack(side="left", padx=(5,0))
    
    def update_gallery_path(self, *args):
        library = self.library_path.get()
        if library:
            self.gallery_path_display.set(str(Path(library) / "Gallery"))
    
    def update_settings(self):
        self.update_next_switch()
        self.logger.info("Settings updated")
    
    def get_gallery_path(self):
        return Path(self.library_path.get()) / "Gallery"
    
    def get_library_path(self):
        return Path(self.library_path.get())
        
    def browse_path(self, variable):
        path = filedialog.askdirectory()
        if path:
            variable.set(str(Path(path)))
    
    def validate_time_format(self, time_str):
        try:
            datetime.strptime(time_str, "%H:%M")
            return True
        except ValueError:
            return False
    
    def validate_paths(self):
        library = self.get_library_path().resolve()
        gallery = self.get_gallery_path().resolve()
        if library == gallery:
            raise ValueError("Gallery path cannot be the same as library path")
        return True
            
    def load_viewed_photos(self):
        try:
            if Path(LOG_FILE).exists():
                with open(LOG_FILE, 'r') as f:
                    data = json.load(f)
                    return set(data) if isinstance(data, list) else set()
        except Exception as e:
            self.logger.error(f"Error loading viewed photos: {e}")
        return set()
    
    def save_viewed_photos(self):
        try:
            with self.viewed_photos_lock:
                with open(LOG_FILE, 'w') as f:
                    json.dump(list(self.viewed_photos), f)
        except Exception as e:
            self.logger.error(f"Error saving viewed photos: {e}")
            
    def get_switch_times(self):
        try:
            if not self.validate_time_format(self.main_time.get()):
                return []
            
            switches = int(self.switches_per_day.get())
            if switches <= 0 or switches > 100:
                return []
            
            main_hour, main_min = map(int, self.main_time.get().split(':'))
            now = datetime.now()
            main_datetime = now.replace(hour=main_hour, minute=main_min, second=0, microsecond=0)
            
            if switches == 1:
                return [main_datetime]
            
            interval = timedelta(hours=24/switches)
            times = [main_datetime + (interval * i) for i in range(switches)]
            
            # Normalize all times to today for comparison
            today = now.date()
            times = [t.replace(year=today.year, month=today.month, day=today.day) for t in times]
            return sorted(times)
        except Exception as e:
            self.logger.error(f"Error calculating switch times: {e}")
            return []
    
    def update_next_switch(self):
        switch_times = self.get_switch_times()
        if not switch_times:
            self.next_switch.set("No switches scheduled")
            return
        
        now = datetime.now()
        for switch_time in switch_times:
            if switch_time > now:
                self.next_switch.set(switch_time.strftime("%H:%M"))
                return
        
        self.next_switch.set(f"{switch_times[0].strftime('%H:%M')} (tomorrow)")
    
    def check_scheduled_switches(self):
        if self.operation_lock.locked():
            return
            
        switch_times = self.get_switch_times()
        if not switch_times:
            return
        
        now = datetime.now()
        current_time = now.replace(second=0, microsecond=0)
        
        for switch_time in switch_times:
            if switch_time.replace(second=0, microsecond=0) == current_time:
                self.logger.info(f"Scheduled switch at {switch_time.strftime('%H:%M')}")
                self.switch_photos_async()
                break
    
    def iter_photos(self, directory):
        try:
            directory_path = Path(directory)
            if not directory_path.exists():
                self.logger.warning(f"Directory does not exist: {directory_path}")
                return
                
            for file_path in directory_path.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in PHOTO_EXTENSIONS:
                    yield file_path
        except Exception as e:
            self.logger.error(f"Error reading directory {directory}: {e}")
    
    def select_photos(self, library_path, count, mode):
        count = max(1, min(count, 10000))
        library_path = Path(library_path)
        
        def unviewed_photos():
            for photo in self.iter_photos(library_path):
                if self.operation_cancelled.is_set():
                    return
                with self.viewed_photos_lock:
                    if photo.name not in self.viewed_photos:
                        yield photo
        
        if mode == "Random":
            unviewed = list(unviewed_photos())
            if len(unviewed) < count:
                self.logger.info("Resetting history - not enough unviewed photos")
                with self.viewed_photos_lock:
                    self.viewed_photos.clear()
                self.save_viewed_photos()
                unviewed = list(self.iter_photos(library_path))
            return random.sample(unviewed, min(count, len(unviewed)))
        
        else:
            self.logger.info(f"Selecting {count} {mode.lower()} photos (scanning library)...")
            processed = [0]  # Use list for closure modification
            
            def photo_with_date_gen():
                for photo in unviewed_photos():
                    if self.operation_cancelled.is_set():
                        return
                    processed[0] += 1
                    if processed[0] % 5000 == 0:
                        self.logger.info(f"Scanned {processed[0]} photos...")
                        self.save_date_cache()  # Save cache periodically
                    yield (self.get_photo_date(photo), photo)
            
            if mode == "Newest":
                selected = [photo for _, photo in heapq.nlargest(count, photo_with_date_gen())]
            else:
                selected = [photo for _, photo in heapq.nsmallest(count, photo_with_date_gen())]
            
            self.logger.info(f"Scan complete: processed {processed[0]} photos")
            self.save_date_cache()  # Save cache after scan
            
            if len(selected) < count:
                self.logger.info("Resetting history - not enough unviewed photos")
                with self.viewed_photos_lock:
                    self.viewed_photos.clear()
                self.save_viewed_photos()
                
                processed[0] = 0
                def all_photos_with_date():
                    for photo in self.iter_photos(library_path):
                        if self.operation_cancelled.is_set():
                            return
                        processed[0] += 1
                        if processed[0] % 5000 == 0:
                            self.logger.info(f"Scanned {processed[0]} photos...")
                        yield (self.get_photo_date(photo), photo)
                
                if mode == "Newest":
                    selected = [photo for _, photo in heapq.nlargest(count, all_photos_with_date())]
                else:
                    selected = [photo for _, photo in heapq.nsmallest(count, all_photos_with_date())]
                
                self.logger.info(f"Scan complete: processed {processed[0]} photos")
                self.save_date_cache()
            
            return selected
    
    def switch_photos_async(self):
        if not self.operation_lock.acquire(blocking=False):
            self.logger.warning("Operation already in progress")
            return
        
        self.operation_cancelled.clear()
        
        try:
            try:
                count = int(self.photo_count.get())
                if count <= 0:
                    raise ValueError("Photo count must be positive")
                if count > 10000:
                    self.logger.warning(f"Photo count {count} exceeds maximum 10000, using 10000")
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror("Invalid Input", str(e)))
                self.operation_lock.release()
                return
            
            try:
                self.validate_paths()
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror("Invalid Path", str(e)))
                self.operation_lock.release()
                return
            
            self.current_thread = threading.Thread(target=self._switch_photos_worker, daemon=True)
            self.root.after(0, self.start_operation)
            self.current_thread.start()
        except Exception as e:
            self.logger.error(f"Error starting switch photos: {e}")
            self.operation_lock.release()
    
    def _switch_photos_worker(self):
        try:
            self.logger.info("Starting photo switch...")
            
            library_path = self.get_library_path()
            gallery_path = self.get_gallery_path()
            
            if not library_path.exists():
                raise Exception(f"Library path does not exist: {library_path}")
            
            gallery_path.mkdir(parents=True, exist_ok=True)
            
            moved_back = 0
            deleted_dupes = []
            
            for photo in self.iter_photos(gallery_path):
                if self.operation_cancelled.is_set():
                    return
                new_path = library_path / photo.name
                try:
                    if new_path.exists():
                        photo.unlink()
                        deleted_dupes.append(photo.name)
                    else:
                        photo.rename(new_path)
                        moved_back += 1
                except Exception as e:
                    self.logger.error(f"Error moving {photo}: {e}")
            
            if deleted_dupes:
                with self.viewed_photos_lock:
                    for filename in deleted_dupes:
                        self.viewed_photos.discard(filename)
                self.save_viewed_photos()
                self.logger.info(f"Removed {len(deleted_dupes)} duplicate(s)")
            
            if moved_back > 0:
                self.logger.info(f"Moved {moved_back} photos back to library")
            
            count = int(self.photo_count.get())
            mode = self.selection_mode.get()
            selected = self.select_photos(library_path, count, mode)
            
            moved_to_gallery = 0
            for photo in selected:
                if self.operation_cancelled.is_set():
                    return
                new_path = gallery_path / photo.name
                try:
                    photo.rename(new_path)
                    with self.viewed_photos_lock:
                        self.viewed_photos.add(photo.name)
                    moved_to_gallery += 1
                except Exception as e:
                    self.logger.error(f"Error moving {photo}: {e}")
            
            self.save_viewed_photos()
            self.logger.info(f"Switch complete: moved {moved_to_gallery} photos to gallery")
            self.root.after(0, lambda: self.end_operation(f"Switched {moved_to_gallery} photos"))
            
        except Exception as e:
            self.logger.error(f"Error switching photos: {e}")
            self.root.after(0, lambda: self.end_operation(f"Error: {str(e)}"))
        finally:
            self.operation_lock.release()
    
    def clear_gallery_async(self):
        if not self.operation_lock.acquire(blocking=False):
            self.logger.warning("Operation already in progress")
            return
        
        self.operation_cancelled.clear()
        
        try:
            try:
                self.validate_paths()
            except ValueError as e:
                self.root.after(0, lambda: messagebox.showerror("Invalid Path", str(e)))
                self.operation_lock.release()
                return
            
            self.current_thread = threading.Thread(target=self._clear_gallery_worker, daemon=True)
            self.root.after(0, self.start_operation)
            self.current_thread.start()
        except Exception as e:
            self.logger.error(f"Error starting clear gallery: {e}")
            self.operation_lock.release()
    
    def _clear_gallery_worker(self):
        try:
            self.logger.info("Starting clear gallery...")
            
            library_path = self.get_library_path()
            gallery_path = self.get_gallery_path()
            
            if not gallery_path.exists():
                self.root.after(0, lambda: self.end_operation("Gallery directory does not exist"))
                return
            
            count = 0
            deleted_dupes = []
            
            for photo in self.iter_photos(gallery_path):
                if self.operation_cancelled.is_set():
                    return
                new_path = library_path / photo.name
                try:
                    if new_path.exists():
                        photo.unlink()
                        deleted_dupes.append(photo.name)
                    else:
                        photo.rename(new_path)
                        count += 1
                except Exception as e:
                    self.logger.error(f"Error moving {photo}: {e}")
            
            if deleted_dupes:
                with self.viewed_photos_lock:
                    for filename in deleted_dupes:
                        self.viewed_photos.discard(filename)
                self.save_viewed_photos()
                self.logger.info(f"Removed {len(deleted_dupes)} duplicate(s)")
            
            self.logger.info(f"Clear complete: moved {count} photos back")
            self.root.after(0, lambda: self.end_operation(f"Moved {count} photos back"))
        except Exception as e:
            self.logger.error(f"Error clearing gallery: {e}")
            self.root.after(0, lambda: self.end_operation(f"Error: {str(e)}"))
        finally:
            self.operation_lock.release()
    
    def reset_history(self):
        if self.operation_lock.locked():
            messagebox.showwarning("Operation in Progress", "Cannot reset during operation")
            return
        
        with self.viewed_photos_lock:
            self.viewed_photos.clear()
        self.save_viewed_photos()
        
        # Also clear date cache
        self.date_cache.clear()
        self.cache_dirty = True
        self.save_date_cache()
        
        self.logger.info("History and cache reset")
    
    def start_operation(self):
        self.status.set("Working...")
        self.progress.start()
        self.switch_btn.config(state="disabled")
        self.clear_btn.config(state="disabled")
        self.reset_btn.config(state="disabled")
    
    def end_operation(self, message):
        self.status.set(message)
        self.progress.stop()
        self.switch_btn.config(state="normal")
        self.clear_btn.config(state="normal")
        self.reset_btn.config(state="normal")
        self.update_next_switch()
    
    def periodic_update(self):
        self.check_scheduled_switches()
        self.update_next_switch()
        self.root.after(60000, self.periodic_update)
    
    def on_closing(self):
        self.operation_cancelled.set()
        if self.current_thread and self.current_thread.is_alive():
            self.current_thread.join(timeout=2.0)
        self.save_date_cache()  # Save cache on exit
        self.root.destroy()
    
    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = PhotoScheduler()
    app.run()
