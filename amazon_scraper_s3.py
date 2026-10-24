import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import time
import random
import re
import os
from multiprocessing import Process, Manager, Lock
import boto3
from botocore.exceptions import NoCredentialsError

# AWS KİMLİK VE BUCKET BİLGİLERİ (Müşterinizin Bilgilerine Göre Doldurun)
AWS_ACCESS_KEY = "BURAYA_AWS_ACCESS_KEY_ID_YAZIN"
AWS_SECRET_KEY = "BURAYA_AWS_SECRET_ACCESS_KEY_YAZIN"
AWS_BUCKET_NAME = "BURAYA_BUCKET_ADINI_YAZIN"
AWS_REGION = "eu-central-1" # Örn: Frankfurt ise eu-central-1, İrlanda ise eu-west-1

def upload_to_aws_s3(local_file_path, bucket_name, s3_object_name):
    """Oluşan güncel XML dosyasını otomatik olarak AWS S3'e yükler."""
    print(f"\n☁️ [AWS S3] Yükleme işlemi başlatılıyor: {local_file_path} -> S3://{bucket_name}/{s3_object_name}")
    try:
        s3 = boto3.client(
            's3',
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            region_name=AWS_REGION
        )
        
        s3.upload_file(
            local_file_path, 
            bucket_name, 
            s3_object_name,
            ExtraArgs={
                'ContentType': 'text/xml',
                'ACL': 'public-read'
            }
        )
        
        public_url = f"https://{bucket_name}.s3.{AWS_REGION}.amazonaws.com/{s3_object_name}"
        print(f"✅ [AWS S3 BAŞARILI] XML Başarıyla Web'e Fırlatıldı!")
        print(f"🔗 PAZARAPI PANELİNE GİRİLECEK XML URL: {public_url}\n")
        return True

    except NoCredentialsError:
        print("❌ [AWS HATA] AWS Kimlik bilgileri (Credentials) bulunamadı.")
        return False
    except Exception as e:
        print(f"❌ [AWS HATA] Dosya S3'e yüklenirken bir sorun oluştu: {e}")
        return False

def extract_asin_from_text(text):
    if not text:
        return ""
    dp_match = re.search(r'/dp/([A-Z0-9]{10})', text)
    if dp_match:
        return dp_match.group(1)
    gp_match = re.search(r'/gp/product/([A-Z0-9]{10})', text)
    if gp_match:
        return gp_match.group(1)
    return ""

def create_initial_xml_file(filename):
    root = ET.Element("ProductsList")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ", level=0)
    tree.write(filename, encoding="utf-8", xml_declaration=True)
    print(f"[SİSTEM] Güvenli XML veri havuzu oluşturuldu: {filename}")

def append_products_to_xml(filename, category_products_list, disk_lock):
    if not category_products_list:
        return False

    with disk_lock:
        try:
            tree = ET.parse(filename)
            root = tree.getroot()

            for prod in category_products_list:
                product_node = ET.SubElement(root, "Product")
                ET.SubElement(product_node, "ProductID").text = prod["ASIN"]
                ET.SubElement(product_node, "ProductName").text = prod["Title"]
                ET.SubElement(product_node, "ProductDescription").text = prod["Title"]
                ET.SubElement(product_node, "CategoryName").text = prod["Category"]
                ET.SubElement(product_node, "Brand").text = prod["Brand"]
                ET.SubElement(product_node, "Photo").text = prod["Image"]
                
                images_node = ET.SubElement(product_node, "Images")
                ET.SubElement(images_node, "Image1").text = prod["Image"]
                
                ET.SubElement(product_node, "Stock").text = str(prod["Stock"])
                ET.SubElement(product_node, "StockCode").text = prod["ASIN"]
                ET.SubElement(product_node, "Barcode").text = f"exml{prod['ASIN'].upper()}f53"
                ET.SubElement(product_node, "Currency").text = "TRY"
                ET.SubElement(product_node, "Price").text = prod["Price"]

            ET.indent(tree, space="  ", level=0)
            tree.write(filename, encoding="utf-8", xml_declaration=True)
            return True
                
        except Exception as e:
            print(f"[KRİTİK HATA] Veriler XML dosyasına yazılırken teknik sorun oluştu: {e}")
            return False

def worker_scrape_category(clean_cat_name, base_url, output_file_name, scraped_asins_global, category_stats, disk_lock):
    print(f"\n📂 [KATEGORİ BAŞLADI]: '{clean_cat_name}'")
    current_url = base_url
    page_num = 1
    category_products_buffer = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--start-maximized"])
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="tr-TR"
        )
        page = context.new_page()

        try:
            page.goto("https://amazon.com.tr", wait_until="domcontentloaded", timeout=45000)
            time.sleep(2)
        except Exception:
            pass
            
        while current_url:
            try:
                print(f"   📄 [{clean_cat_name}] Sayfa {page_num} yükleniyor...")
                
                try:
                    page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
                except Exception:
                    time.sleep(3)
                    try:
                        page.goto(current_url, wait_until="domcontentloaded", timeout=60000)
                    except Exception:
                        break
                
                for scroll_step in range(5):
                    page.evaluate(f"window.scrollTo(0, (document.body.scrollHeight / 5) * {scroll_step + 1});")
                    time.sleep(0.4)
                
                page_source = page.content()
                soup = BeautifulSoup(page_source, "html.parser")
                
                products = []
                raw_candidates = soup.find_all(lambda tag: tag.name == 'div' and (
                    tag.has_attr('data-asin') or tag.has_attr('data-cy') or 
                    (tag.has_attr('class') and any('SearchResultItem' in c or 'ProductBlock' in c or 'SearchResultCard' in c for c in tag['class']))
                ))
                raw_candidates.extend(soup.select("li.a-carousel-card, ol.a-ordered-list li, div.octopus-pc-item"))
                
                seen_nodes = set()
                for node in raw_candidates:
                    node_id = id(node)
                    if node_id not in seen_nodes:
                        products.append(node)
                        seen_nodes.add(node_id)
                
                for prod in products:
                    try:
                        asin = ""
                        if prod.has_attr('data-asin') and prod.get('data-asin').strip():
                            asin = prod.get('data-asin').strip()
                        elif prod.has_attr('data-csa-c-item-id') and "asin." in prod.get('data-csa-c-item-id'):
                            asin = prod.get('data-csa-c-item-id').split("asin.")[-1].strip()
                        
                        if not asin or len(asin) != 10:
                            links = prod.find_all("a", href=True)
                            for link in links:
                                potential_asin = extract_asin_from_text(link.get("href"))
                                if potential_asin:
                                    asin = potential_asin
                                    break
                        
                        if not asin or len(asin) != 10 or asin in scraped_asins_global:
                            continue

                        product_name = ""
                        title_selectors = [
                            "h2.a-size-base-plus", "span.a-truncate-full",
                            "div.acsProductBlockV1__product-title span",
                            "div._cDEzb_p13n-sc-css-line-clamp-1_1Fn8Z",
                            "div._cDEzb_p13n-sc-css-line-clamp-2_EWgCb",
                            "h2.a-size-mini", "div.octopus-pc-item-title"
                        ]
                        for selector in title_selectors:
                            title_el = prod.select_one(selector)
                            if title_el and title_el.text.strip():
                                product_name = title_el.text.strip()
                                break
                        if not product_name:
                            img_search = prod.find("img")
                            if img_search and img_search.get("alt"):
                                product_name = img_search.get("alt").strip()
                        if not product_name:
                            continue

                        brand_name = ""
                        brand_selectors = ["span.a-size-base-plus.a-color-base", "h3 span", "span.a-color-secondary"]
                        for b_sel in brand_selectors:
                            b_el = prod.select_one(b_sel)
                            if b_el and b_el.text.strip() and len(b_el.text.strip()) < 20 and b_el.text.strip() not in product_name:
                                brand_name = b_el.text.strip().upper()
                                break
                        
                        if not brand_name or brand_name == "N/A":
                            words = product_name.split()
                            brand_name = ''.join(e for e in words if e.isalnum()).upper() if words else "AMAZON_MARKET"
                            if len(brand_name) < 2:
                                brand_name = "AMAZON_MARKET"

                        img_el = prod.find("img")
                        photo_url = img_el.get("src") if img_el else ""

                        price = 0.00
                        price_text = ""
                        offscreen_price = prod.select_one("span.a-price span.a-offscreen")
                        if offscreen_price:
                            price_text = offscreen_price.text.strip()
                        else:
                            price_el = (prod.find("span", class_="a-color-price") or 
                                        prod.find("span", class_="p13n-sc-price") or 
                                        prod.find("span", class_="a-price-whole"))
                            if price_el:
                                price_text = price_el.text.strip()
                        
                        if price_text:
                            raw_price = price_text.replace("TL", "").replace("TRY", "").replace(".", "").replace(",", ".").replace(" ", "").strip()
                            try:
                                price = float(raw_price)
                            except ValueError:
                                pass

                        detected_stock = 21
                        prod_full_text = prod.get_text(" ", strip=True)
                        
                        patterns = [
                            r"stokta\s+sadece\s+(\d+)\s*(?:adet)?\s*kaldı",
                            r"son\s+(\d+)\s*(?:ürün|adet)",
                            r"tükenmek\s+üzere\s*\(?(\d+)\s*(?:adet)?\)?",
                            r"(\d+)\s+adet\s+kaldı"
                        ]
                        
                        for pattern in patterns:
                            match = re.search(pattern, prod_full_text, re.IGNORECASE)
                            if match:
                                detected_stock = int(match.group(1))
                                break

                        category_products_buffer.append({
                            "ASIN": asin,
                            "Title": product_name,
                            "Category": clean_cat_name,
                            "Brand": brand_name,
                            "Image": photo_url,
                            "Price": f"{price:.2f}",
                            "Stock": detected_stock 
                        })
                        scraped_asins_global.add(asin)

                    except Exception:
                        continue
                
                next_page_button = soup.select_one("a.s-pagination-next")
                if next_page_button and next_page_button.get("href") and not "s-pagination-disabled" in next_page_button.get("class", []):
                    next_href = next_page_button.get("href")
                    current_url = "https://amazon.com.tr" + next_href if next_href.startswith("/") else next_href
                    page_num += 1
                    time.sleep(random.uniform(3.0, 5.0))
                else:
                    break

            except Exception:
                break
        
        browser.close()

    if category_products_buffer:
        is_saved = append_products_to_xml(output_file_name, category_products_buffer, disk_lock)
        if is_saved:
            category_stats[clean_cat_name] = len(category_products_buffer)
            print(f"💾 [TOPLU XML KAYDI BİTTİ] '{clean_cat_name}' tamamlandı. {len(category_products_buffer)} ürün XML'e eklendi!")
        else:
            category_stats[clean_cat_name] = 0
    else:
        category_stats[clean_cat_name] = 0

def scrape_amazon_parallel_control(target_links_dict, max_workers=2):
    output_file_name = "amazon_dropshipping_feed.xml"
    
    print(f"\n==================================================================")
    print(f"🚀 [BAŞLANGIÇ] AWS S3 Entegreli Çok Kanallı Paralel Motor Aktif.")
    print(f"💾 YEREL DOSYA: {output_file_name}")
    print(f"==================================================================")
    
    if os.path.exists(output_file_name):
        try: os.remove(output_file_name)
        except: pass
        
    create_initial_xml_file(output_file_name)
    
    with Manager() as manager:
        scraped_asins_global = manager.set()
        category_stats = manager.dict()
        disk_lock = Lock()
        
        items = list(target_links_dict.items())
        active_processes = []
        
        while len(items) > 0 or len(active_processes) > 0:
            while len(active_processes) < max_workers and len(items) > 0:
                cat_name, cat_url = items.pop(0)
                p = Process(
                    target=worker_scrape_category, 
                    args=(cat_name, cat_url, output_file_name, scraped_asins_global, category_stats, disk_lock)
                )
                active_processes.append(p)
                p.start()
                time.sleep(2)
            
            for p in active_processes:
                if not p.is_alive():
                    p.join()
                    active_processes.remove(p)
            time.sleep(1)

        print(f"\n==================================================================")
        print(f"🎉 TÜM PARALEL KATEGORİ TARAMALARI YERELDE TAMAMLANDI!")
        print(f"==================================================================")
        total_success_count = 0
        for cat, count in category_stats.items():
            print(f" 📂 Kategori: '{cat:<30}' -> Havuza Eklenen: {count} adet")
            total_success_count += count
        print(f"------------------------------------------------------------------")
        print(f" 🚀 TOPLAM GÜVENCE ALTINA ALINAN BENZERSİZ ÜRÜN: {total_success_count} ADET")
        print(f"==================================================================")
        
        if total_success_count > 0 and os.path.exists(output_file_name):
            s3_dest_name = "amazon_dropshipping_feed.xml"
            upload_to_aws_s3(output_file_name, AWS_BUCKET_NAME, s3_dest_name)

if __name__ == '__main__':
    AMAZON_LINKS_MAP = {
        "Ayakkabı":    "https://www.amazon.com.tr/s?i=fashion&rh=n%3A13546687031&s=popularity-rank&fs=true&ref=lp_13546687031_sar",
        "Bahçe":        "https://www.amazon.com.tr/s?i=garden&rh=n%3A21324944031&s=popularity-rank&fs=true&ref=lp_21324944031_sar",
        "Elektronik":  "https://www.amazon.com.tr/s?i=electronics&rh=n%3A12466496031&s=popularity-rank&fs=true&ref=lp_12466496031_sar", 
        "Ev ve Yaşam": "https://www.amazon.com.tr/s?i=home&rh=n%3A12466667031&s=popularity-rank&fs=true&ref=lp_12466667031_sar",
        "Bebek":       "https://www.amazon.com.tr/s?i=baby&rh=n%3A12466208031&s=popularity-rank&fs=true&ref=lp_12466208031_sar",
        "Bilgisayar":  "https://www.amazon.com.tr/s?i=computers&rh=n%3A12466439031&s=popularity-rank&fs=true&ref=lp_12466439031_sar",
        "Evcil Hayvan":"https://www.amazon.com.tr/s?i=pets&rh=n%3A20230635031&s=popularity-rank&fs=true&ref=lp_20230635031_sar",
        "Gıda Ürünleri":"https://www.amazon.com.tr/s?i=grocery&rh=n%3A21680147031&s=popularity-rank&fs=true&ref=lp_21680147031_sar",
        "Kişisel Bakım ve Kozmetik": "https://www.amazon.com.tr/s?i=beauty&rh=n%3A12571731031&s=popularity-rank&fs=true&ref=lp_12571731031_sar",
        "Kitap": "https://www.amazon.com.tr/s?i=stripbooks&rh=n%3A12466380031&s=popularity-rank&fs=true&ref=lp_12466380031_sar",
        "Kadın Moda": "https://www.amazon.com.tr/s?i=fashion&rh=n%3A13546686031&s=popularity-rank&fs=true&ref=lp_13546686031_sar",
        "Erkek Moda": "https://www.amazon.com.tr/s?i=fashion&rh=n%3A13546677031&s=popularity-rank&fs=true&ref=lp_13546677031_sar",
        "Çocuk ve Bebek Moda": "https://www.amazon.com.tr/s?i=fashion&rh=n%3A13546693031&s=popularity-rank&fs=true&ref=lp_13546693031_sar",
        "Mutfak": "https://www.amazon.com.tr/s?i=kitchen&rh=n%3A12466781031&s=popularity-rank&fs=true&ref=lp_12466781031_sar",
        "Müzik Ekipmanları ve Enstrümanları": "https://www.amazon.com.tr/s?i=mi&rh=n%3A21325061031&s=popularity-rank&fs=true&ref=lp_21325061031_sar",
        "Ofis ve Kırtasiye": "https://www.amazon.com.tr/s?i=office-products&rh=n%3A12467009031&s=popularity-rank&fs=true&ref=lp_12467009031_sar",
        "Otomotiv": "https://www.amazon.com.tr/s?i=automotive&rh=n%3A12466266031&s=popularity-rank&fs=true&ref=lp_12466266031_sar",
        "Oyuncak": "https://www.amazon.com.tr/s?i=toys&rh=n%3A12467126031&s=popularity-rank&fs=true&ref=lp_12467126031_sar",
        "Sağlık, Bakım, Temizlik": "https://www.amazon.com.tr/s?i=hpc&rh=n%3A12466610031&s=popularity-rank&fs=true&ref=lp_12466610031_sar",
        "Spor ve Outdoor": "https://www.amazon.com.tr/s?i=sports&rh=n%3A12467068031&s=popularity-rank&fs=true&ref=lp_12467068031_sar",
        "Telefon": "https://www.amazon.com.tr/s?i=electronics&rh=n%3A13710045031&s=popularity-rank&fs=true&ref=lp_13710045031_sar",
        "Televizyonlar ve Ev Sinema Sistemleri": "https://www.amazon.com.tr/s?i=electronics&rh=n%3A13709925031&s=popularity-rank&fs=true&ref=lp_13709925031_sar",
        "Video Oyunu ve Konsol": "https://www.amazon.com.tr/s?i=videogames&rh=n%3A12467183031&s=popularity-rank&fs=true&ref=lp_12467183031_sar",
        "Yapı Market": "https://www.amazon.com.tr/s?i=diy&rh=n%3A12466724031&s=popularity-rank&fs=true&ref=lp_12466724031_sar",
    }
    
    scrape_amazon_parallel_control(AMAZON_LINKS_MAP, max_workers=8)
