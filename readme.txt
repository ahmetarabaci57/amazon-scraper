# Multi-Threaded Amazon Scraper with AWS S3 Integration

A high-performance, industrial-grade data extraction pipeline built with **Python**, **Playwright**, and **BeautifulSoup**. This project utilizes asynchronous multi-processing architecture to scrape product data from Amazon TR concurrently, processes dynamic stock alerts using advanced regular expressions, and automatically syncs the output to **AWS S3** for seamless dropshipping or e-commerce integration (e.g., PazarApi).

## 🚀 Key Features

- **Parallel Processing Architecture:** Leverages `multiprocessing` to spin up multiple independent Chromium instances (`max_workers`), dramatically cutting down total scraping time.
- **Dynamic Stock Analytics:** Bypasses static layouts by evaluating full-text product fields using RegEx to capture urgent stock patterns (`"Only X left in stock"`, `"Last X products"`) with a safe default fallback (`21 items`).
- **Global Duplicate Protection:** Uses a shared inter-process memory buffer (`multiprocessing.Manager.set`) to guarantee 100% unique data collection across all parallel streams with zero ASIN duplicates.
- **Optimized Batch File I/O:** Buffers product payloads locally per category and dumps them in single operations using a strict process `Lock` mechanism to eliminate disk write bottlenecks.
- **Automated AWS S3 Cloud Sync:** Automatically streams the finalized XML feed to AWS Cloud Storage via `boto3`, preserving public access controls and exact content-types for external API integration.

## 🛠️ Architecture Workflow

1. **Initialization:** Clears previous local runs and creates a fresh XML structure with safe encoding (`utf-8`).
2. **Concurreny Engine:** Assigns defined categories to distributed worker processes based on system resources (`max_workers`).
3. **Scraping & Parsing:** Playwright manages human-like interaction (scrolling, timeouts) while BeautifulSoup handles node extraction.
4. **Data Assembly:** Extracted items are buffered into category-specific memories while tracking duplicate global ASINs.
5. **Disk Write & Cloud Upload:** Writes results safely using a shared disk-lock, then pushes the single production-ready `amazon_dropshipping_feed.xml` to AWS S3.

## ⚙️ Installation & Setup

### 1. Prerequisites
Ensure you have Python 3.8+ installed along with the required web automation and cloud dependencies:

```bash
pip install playwright beautifulsoup4 boto3
playwright install chromium
```

### 2. AWS S3 Configuration
Open `amazon_scraper_s3.py` and configure your cloud bucket credentials at the top of the file:

```python
AWS_ACCESS_KEY = "YOUR_AWS_ACCESS_KEY_ID"
AWS_SECRET_KEY = "YOUR_AWS_SECRET_ACCESS_KEY"
AWS_BUCKET_NAME = "YOUR_BUCKET_NAME"
AWS_REGION = "eu-central-1"  # Replace with your bucket's region
```

### 3. Running the Scraper
Execute the controller script to start parallel browser crawling:

```bash
python amazon_scraper_s3.py
```

## 📊 Performance Configuration
You can easily scale the browser channels based on your host CPU and RAM availability by adjusting the `max_workers` flag in the main runtime hook:

```python
if __name__ == '__main__':
    # Adjust max_workers (e.g., 2, 3, or 4 concurrent browsers)
    scrape_amazon_parallel_control(AMAZON_LINKS_MAP, max_workers=3)
```

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
