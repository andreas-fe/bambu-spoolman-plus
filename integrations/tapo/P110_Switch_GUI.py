from pathlib import Path
from tapo_settings import safe_off
# Tapo P110 Steuerung GUI mit Tkinter und asyncio
# Diese Anwendung ermöglicht die Steuerung eines Tapo P110 Smart Plugs
# und bietet eine einfache Benutzeroberfläche zur Anzeige von Statusinformationen.
# Die Anwendung verwendet asyncio für asynchrone Operationen und pystray für ein Tray-Icon.
# Die GUI ist in Tkinter implementiert und ermöglicht das Ein- und Ausschalten des Geräts,
# das Abrufen von Leistungsdaten und die Anzeige von Verbrauchsinformationen.
# Die Anwendung speichert Benutzerdaten in einer JSON-Datei und bietet eine einklappbare Ansicht für die Eingabefelder.
# Die Anwendung kann minimiert werden und bietet ein Tray-Icon zur Wiederherstellung.
# Anforderungen:
# pip install tapo pystray pillow asyncio tkinter
# oder pip install -r requirements.txt
# Autor: Andreas Fettig - Copyright (c) 2025

import tkinter as tk
from tkinter import messagebox, ttk
import json
import os
import asyncio
import threading
from tapo import ApiClient
import time
from PIL import Image, ImageTk


class CollapsibleFrame(ttk.Frame):
    def __init__(self, parent, text="", *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.show = tk.BooleanVar(value=False)  # Standardmäßig eingeklappt
        self.header = ttk.Frame(self)
        self.header.pack(fill="x", expand=1)
        ttk.Label(self.header, text=text, font=('Arial', 10, 'bold')).pack(side="left", padx=5)
        self.toggle_button = ttk.Button(self.header, width=2, text='+', command=self.toggle)
        self.toggle_button.pack(side="right", padx=5)
        self.content = ttk.Frame(self)
        self.toggle()

    def toggle(self):
        if self.show.get():
            self.content.pack(fill="x", expand=1)
            self.toggle_button.config(text='−')
            self.show.set(False)
        else:
            self.content.forget()
            self.toggle_button.config(text='+')
            self.show.set(True)

class TapoControlApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tapo P110 Steuerung")
        self.geometry("400x300")

        # Fenstericon setzen
        try:
            icon_img = Image.open("tapo_p110.png")
            icon_img = icon_img.resize((32, 32), Image.Resampling.LANCZOS)
            self.icon_tk = ImageTk.PhotoImage(icon_img)
            self.iconphoto(True, self.icon_tk)
        except Exception as e:
            print(f"Fenstericon konnte nicht gesetzt werden: {e}")

        # Tray-Icon Bild (PIL.Image)
        try:
            self.tray_img = Image.open("tapo_p110.png").resize((32, 32), Image.Resampling.LANCZOS)
        except Exception as e:
            print(f"Tray-Icon Bild konnte nicht geladen werden: {e}")
            self.tray_img = None

        # Event Loop für asyncio
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.start_loop, daemon=True)
        self.thread.start()

        # Eingabefelder Frame (einklappbar)
        self.input_frame = CollapsibleFrame(self, text="Geräte-Einstellungen")
        self.input_frame.pack(fill="x", padx=10, pady=10)
        
        ttk.Label(self.input_frame.content, text="Benutzername:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.username_entry = ttk.Entry(self.input_frame.content, width=30)
        self.username_entry.grid(row=0, column=1, padx=5, pady=5)
        ttk.Label(self.input_frame.content, text="Passwort:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.password_entry = ttk.Entry(self.input_frame.content, show="*", width=30)
        self.password_entry.grid(row=1, column=1, padx=5, pady=5)
        ttk.Label(self.input_frame.content, text="IP-Adresse:").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.ip_entry = ttk.Entry(self.input_frame.content, width=30)
        self.ip_entry.grid(row=2, column=1, padx=5, pady=5)
        self.save_button = ttk.Button(self.input_frame.content, text="Daten speichern", command=self.save_credentials)
        self.save_button.grid(row=3, column=0, columnspan=2, pady=10)

        # Frame für Status und Bild nebeneinander
        status_image_frame = ttk.Frame(self)
        status_image_frame.pack(fill="both", expand=1, padx=10, pady=10)

        # Status Frame (links im status_image_frame)
        status_frame = ttk.LabelFrame(status_image_frame, text="Gerätestatus")
        status_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=0)
        status_image_frame.columnconfigure(0, weight=3)
        status_image_frame.columnconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="Status: Nicht verbunden")
        ttk.Label(status_frame, textvariable=self.status_var, font=("Arial", 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Separator(status_frame, orient="horizontal").grid(row=1, column=0, columnspan=2, sticky="ew", pady=5)
        ttk.Label(status_frame, text="Aktuelle Leistung:").grid(row=2, column=0, sticky="w", padx=5)
        self.power_var = tk.StringVar(value="0 W")
        ttk.Label(status_frame, textvariable=self.power_var).grid(row=2, column=1, sticky="w", padx=5)
        ttk.Label(status_frame, text="Laufzeit heute:").grid(row=3, column=0, sticky="w", padx=5)
        self.runtime_var = tk.StringVar(value="0 Minuten")
        ttk.Label(status_frame, textvariable=self.runtime_var).grid(row=3, column=1, sticky="w", padx=5)
        ttk.Label(status_frame, text="Energieverbrauch heute:").grid(row=4, column=0, sticky="w", padx=5)
        self.energy_var = tk.StringVar(value="0 Wh")
        ttk.Label(status_frame, textvariable=self.energy_var).grid(row=4, column=1, sticky="w", padx=5)
        ttk.Label(status_frame, text="Energieverbrauch Monat:").grid(row=5, column=0, sticky="w", padx=5)
        self.energy_month_var = tk.StringVar(value="0 Wh")
        ttk.Label(status_frame, textvariable=self.energy_month_var).grid(row=5, column=1, sticky="w", padx=5)
        ttk.Label(status_frame, text="Zuletzt aktualisiert:").grid(row=6, column=0, sticky="w", padx=5)
        self.last_update_var = tk.StringVar(value="Nie")
        ttk.Label(status_frame, textvariable=self.last_update_var).grid(row=6, column=1, sticky="w", padx=5)

        # Bild rechts neben dem Status
        try:
            bild = Image.open("tapo_p110.png")  # Dateiname ggf. anpassen
            bild = bild.resize((120, 120), Image.Resampling.LANCZOS)
            self.bild_tk = ImageTk.PhotoImage(bild)
            self.bild_label = ttk.Label(status_image_frame, image=self.bild_tk)
            self.bild_label.grid(row=0, column=1, sticky="ne", padx=(10,0))
        except Exception as e:
            print(f"Bild konnte nicht geladen werden: {e}")

        # Steuerungs-Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)
        self.status_button = ttk.Button(button_frame, text="Status abfragen", command=self.get_status)
        self.status_button.pack(side=tk.LEFT, padx=5)
        self.on_button = ttk.Button(button_frame, text="Einschalten", command=self.turn_on)
        self.on_button.pack(side=tk.LEFT, padx=5)
        self.off_button = ttk.Button(button_frame, text="Ausschalten", command=self.turn_off)
        self.off_button.pack(side=tk.LEFT, padx=5)

        # Auto-Refresh Option
        self.auto_refresh_var = tk.BooleanVar(value=False)
        self.auto_refresh_cb = ttk.Checkbutton(
            self, 
            text="Automatische Status-Aktualisierung (alle 10s)",
            variable=self.auto_refresh_var,
            command=self.toggle_auto_refresh
        )
        self.auto_refresh_cb.pack(pady=5)
        self.auto_refresh_id = None

        # Gespeicherte Daten laden
        self.load_credentials()

    def start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def toggle_auto_refresh(self):
        if self.auto_refresh_var.get():
            self.auto_refresh()
        elif self.auto_refresh_id:
            self.after_cancel(self.auto_refresh_id)
            self.auto_refresh_id = None

    def auto_refresh(self):
        self.get_status()
        self.auto_refresh_id = self.after(10000, self.auto_refresh)

    def get_status(self):
        asyncio.run_coroutine_threadsafe(self.async_get_status(), self.loop)

    async def async_get_status(self):
        try:
            client = ApiClient(
                self.username_entry.get(),
                self.password_entry.get()
            )
            device = await client.p110(self.ip_entry.get())
            status = await device.get_device_info()
            self.status_var.set(f"Status: {'EIN' if status.device_on else 'AUS'}")
            try:
                current_power = await device.get_current_power()
                power_data = current_power.to_dict()
                current_power_mw = power_data.get('current_power', 0)
                self.power_var.set(f"{current_power_mw} W")
            except Exception as e:
                self.power_var.set("Nicht verfügbar")
                print(f"Current power error: {e}")
            try:
                energy_usage = await device.get_energy_usage()
                usage_data = energy_usage.to_dict()
                today_runtime = usage_data.get('today_runtime', 0)
                self.runtime_var.set(f"{today_runtime} min")
                today_energy = usage_data.get('today_energy', 0)
                self.energy_var.set(f"{today_energy} Wh")
                month_energy = usage_data.get('month_energy', 0)
                self.energy_month_var.set(f"{month_energy} Wh")
            except Exception as e:
                self.runtime_var.set("Nicht verfügbar")
                self.energy_var.set("Nicht verfügbar")
                self.energy_month_var.set("Nicht verfügbar")
                print(f"Energy usage error: {e}")
            self.last_update_var.set(time.strftime("%H:%M:%S"))
        except Exception as e:
            self.status_var.set(f"Fehler: {str(e)}")
            self.last_update_var.set("Fehler bei Aktualisierung")
            print(f"General error: {e}")

    def turn_on(self):
        asyncio.run_coroutine_threadsafe(self.async_turn_on(), self.loop)

    async def async_turn_on(self):
        try:
            client = ApiClient(
                self.username_entry.get(),
                self.password_entry.get()
            )
            device = await client.p110(self.ip_entry.get())
            await device.on()
            self.status_var.set("Status: EINGESCHALTET")
            await asyncio.sleep(2)
            asyncio.run_coroutine_threadsafe(self.async_get_status(), self.loop)
        except Exception as e:
            self.status_var.set(f"Fehler: {str(e)}")

    def turn_off(self):
        asyncio.run_coroutine_threadsafe(self.async_turn_off(), self.loop)

    async def async_turn_off(self):
        try:
            client = ApiClient(
                self.username_entry.get(),
                self.password_entry.get()
            )
            device = await client.p110(self.ip_entry.get())
            await safe_off(device)
            self.status_var.set("Status: AUSGESCHALTET")
            await asyncio.sleep(2)
            asyncio.run_coroutine_threadsafe(self.async_get_status(), self.loop)
        except Exception as e:
            self.status_var.set(f"Fehler: {str(e)}")

    def save_credentials(self):
        data = {
            "username": self.username_entry.get(),
            "password": self.password_entry.get(),
            "ip": self.ip_entry.get()
        }
        with open(str(Path(os.getenv("TAPO_CONFIG_FILE", Path(__file__).with_name("tapo_config.json")))), "w") as f:
            json.dump(data, f)
        messagebox.showinfo("Erfolg", "Daten erfolgreich gespeichert!")

    def load_credentials(self):
        if os.path.exists(str(Path(os.getenv("TAPO_CONFIG_FILE", Path(__file__).with_name("tapo_config.json"))))):
            with open(str(Path(os.getenv("TAPO_CONFIG_FILE", Path(__file__).with_name("tapo_config.json")))), "r") as f:
                data = json.load(f)
                self.username_entry.insert(0, data.get("username", ""))
                self.password_entry.insert(0, data.get("password", ""))
                self.ip_entry.insert(0, data.get("ip", ""))

if __name__ == "__main__":
    app = TapoControlApp()
    app.mainloop()
