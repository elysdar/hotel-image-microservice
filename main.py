import os
import time
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

app = FastAPI()

CACHE_DIR = "image_cache"
ORIGINALS_DIR = "hotel_images"

# Конфигурация инвалидации кэша: время жизни файлов 24 часа (в секундах)
CACHE_TTL_SECONDS = 86400

# Разрешенные форматы исходных изображений
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(ORIGINALS_DIR, exist_ok=True)


# 1. Основной эндпоинт для отдачи и ресайза изображений (GET)
@app.get("/image")
def get_image(w: int, h: int, fmt: str, src: str):
    # Валидация целевого формата (по ТЗ строго WebP)
    if fmt.lower() != "webp":
        raise HTTPException(status_code=400, detail="Поддерживается только формат webp")

    # ВАЛИДАЦИЯ 1: Проверка расширения входящего файла
    _, ext = os.path.splitext(src.lower())
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый формат файла '{ext}'. Сервер обрабатывает только: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    original_path = os.path.join(ORIGINALS_DIR, src)
    if not os.path.exists(original_path):
        raise HTTPException(status_code=404, detail="Оригинальное изображение не найдено")

    # ВАЛИДАЦИЯ 2: Проверка физической целостности и структуры картинки
    try:
        with Image.open(original_path) as verify_img:
            verify_img.verify()  # Быстрая проверка, что файл не битый
    except Exception:
        raise HTTPException(status_code=400, detail="Файл поврежден или не является валидным изображением JPEG/PNG")

    # --- НАЧАЛО ИЗМЕНЕНИЙ: Проверка по размеру против туннелирования macOS ---
    
    # Получаем размер оригинального файла в байтах
    original_size = os.path.getsize(original_path)

    # Добавляем размер (original_size) в имя кэш-файла
    cache_filename = f"{src}_{w}x{h}_{original_size}.{fmt}"
    cache_path = os.path.join(CACHE_DIR, cache_filename)

    # Заголовки, которые ЗАПРЕЩАЮТ браузеру на Mac кэшировать картинку
    no_browser_cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    }

    # Проверка существования и актуальности кэша
    if os.path.exists(cache_path):
        cache_mtime = os.path.getmtime(cache_path)
        original_mtime = os.path.getmtime(original_path)
        file_age = time.time() - cache_mtime

        # Кэш инвалидируется, если он старше TTL ИЛИ если оригинал обновился позже создания кэша
        if file_age > CACHE_TTL_SECONDS or original_mtime > cache_mtime:
            try:
                os.remove(cache_path)
            except OSError:
                pass  # Игнорируем ошибку, если файл уже удален другим потоком
        else:
            # Кэш актуален (ГОРЯЧИЙ ЗАПРОС) + запрещаем кэш браузера
            return FileResponse(
                cache_path, 
                media_type="image/webp", 
                headers={"X-Cache": "HIT", **no_browser_cache_headers}
            )      

    # Обработка «на лету» (ХОЛОДНЫЙ ЗАПРОС)
    try:
        # Re-open необходим, так как verify() закрывает дескрипторы файла
        with Image.open(original_path) as img:
            img_resized = img.resize((w, h), Image.Resampling.LANCZOS)
            img_resized.save(cache_path, format="WEBP", quality=80)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {str(e)}")

    # Возвращаем созданный файл (ХОЛОДНЫЙ ЗАПРОС) + запрещаем кэш браузера
    return FileResponse(
        cache_path, 
        media_type="image/webp", 
        headers={"X-Cache": "MISS", **no_browser_cache_headers}
    )


# 2. Эндпоинт для принудительной инвалидации кэша (DELETE)
@app.delete("/image")
def invalidate_cache(src: str):
    if not src:
        raise HTTPException(status_code=400, detail="Параметр src не может быть пустым")

    deleted_count = 0
    for filename in os.listdir(CACHE_DIR):
        if filename.startswith(f"{src}_"):
            file_path = os.path.join(CACHE_DIR, filename)
            try:
                os.remove(file_path)
                deleted_count += 1
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Не удалось удалить файл {filename}: {str(e)}")

    if deleted_count == 0:
        return {"status": "success", "message": f"Кэш для файла {src} уже пуст или не существовал"}

    return {"status": "success", "message": f"Инвалидация выполнена успешно. Удалено файлов из кэша: {deleted_count}"}
