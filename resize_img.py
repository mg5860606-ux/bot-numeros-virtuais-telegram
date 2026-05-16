from PIL import Image

def resize_image(input_path, output_path, size=(640, 360)):
    with Image.open(input_path) as img:
        # Redimensiona usando Lanczos para manter a qualidade
        resized_img = img.resize(size, Image.Resampling.LANCZOS)
        resized_img.save(output_path)
        print(f"Imagem salva em {output_path} com tamanho {size}")

if __name__ == "__main__":
    resize_image("c:/Bot-numeros-virtuais/desc_bot.png", "c:/Bot-numeros-virtuais/desc_bot_640x360.png")
