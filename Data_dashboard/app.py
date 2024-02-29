from flask import Flask, render_template, request, redirect, flash, session
import pyodbc
from bs4 import BeautifulSoup
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # Set the backend to 'Agg' before importing pyplot
import matplotlib.pyplot as plt
from io import BytesIO
import base64
import re

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Replace these values with your SQL Server details
server = 'ABINA\\SQLEXPRESS'
database = 'master'
conn_str = f'DRIVER=SQL Server;SERVER={server};DATABASE={database};Trusted_Connection=yes;'

# Function to establish database connection
def get_connection():
    return pyodbc.connect(conn_str)


def clean_price(price):
    # Extract the numerical part by removing the first two characters (assumed to be '£')
    cleaned_price = price[2:]

    try:
        # Convert the cleaned price to float
        return float(cleaned_price)
    except ValueError:
        # Handle cases where the price string cannot be converted to float
        return None

def scrape_website():
    base_url = 'https://books.toscrape.com/'
    books = []
    # Iterate over all pages
    for page_num in range(1, 2):
        url = f'{base_url}catalogue/page-{page_num}.html'
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')

        # Extracting data
        for article in soup.find_all('article', class_='product_pod'):
            title = article.h3.a['title']
            price_raw = article.find('p', class_='price_color').text
            price_cleaned = clean_price(price_raw)
            availability = article.find('p', class_='instock availability').text.strip()
            books.append({'title': title, 'price': price_cleaned, 'availability': availability})

    return books


def create_or_update_favorites(title, price, availability):
    conn = get_connection()
    cursor = conn.cursor()

    # Check if the favorites table exists, create it if it doesn't
    cursor.execute("IF NOT EXISTS (SELECT * FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME = 'favorites') CREATE TABLE favorites (id INT PRIMARY KEY IDENTITY(1,1), title VARCHAR(255), price VARCHAR(50), availability VARCHAR(50))")

    # Check if the record already exists in the favorites table
    cursor.execute("SELECT * FROM favorites WHERE title = ?", (title,))
    existing_record = cursor.fetchone()

    if existing_record:
        # If the record exists, update it
        cursor.execute("UPDATE favorites SET price = ?, availability = ? WHERE title = ?", (price, availability, title))
    else:
        # If the record doesn't exist, insert it
        cursor.execute("INSERT INTO favorites (title, price, availability) VALUES (?, ?, ?)", (title, price, availability))

    conn.commit()
    conn.close()


def remove_book(title):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM favorites WHERE title = ?", (title,))
    conn.commit()
    conn.close()

@app.route('/')
def home():
    session.pop('user', None)  # Ensure user is not logged in when visiting home page
    session['page_num'] = 1
    books = scrape_website()
    return render_template('home.html', books=books)
@app.route('/next')
def next_page():
    session['page_num'] += 1
    books = scrape_website()
    return render_template('welcome.html', books=books)

@app.route('/prev')
def prev_page():
    session['page_num'] -= 1
    if session['page_num'] < 1:
        session['page_num'] = 1
    books = scrape_website()
    return render_template('welcome.html', books=books)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        conn = get_connection()
        username = request.form['username']
        password = request.form['password']
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE username = ? AND password = ?', (username, password))
        user = cursor.fetchone()
        if user:
            session['user'] = username
            conn.close()
            return redirect('/welcome')
        else:
            flash('Invalid username or password', 'error')
            conn.close()
    return render_template('login.html')

@app.route('/register')
def register():
    return render_template('register.html')

@app.route('/welcome', methods=['GET', 'POST'])
def welcome():
    if 'user' not in session:
        return redirect('/login')

    books = scrape_website()

    if request.method == 'POST':
        fav_books = [book['title'] for book in books if book['title'] in request.form.getlist('fav')]
        if fav_books:
            for book in books:
                if book['title'] in fav_books:
                    create_or_update_favorites(book['title'], book['price'], book['availability'])
            flash('Added to favorites: {}'.format(', '.join(fav_books)), 'success')

    return render_template('welcome.html', books=books)

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return redirect('/')


@app.route('/remove_favorite', methods=['POST'])
def remove_favorite():
    if request.method == 'POST':
        title = request.form.get('title')
        remove_book(title)
        flash('Book removed successfully!', 'success')
    return redirect('/info')


def fetch_price_data():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title, price FROM favorites")
    data = cursor.fetchall()
    conn.close()

    # Create a DataFrame from the fetched data
    df = pd.DataFrame(data, columns=['Title', 'Price'])

    # Clean and convert the price column to float
    df['Price'] = df['Price'].apply(clean_price)

    return df


def plot_prices(data):
    titles = [row[0] for row in data]
    prices = []

    for row in data:
        price_str = row[1]
        
        cleaned_price_str = price_str.replace('£', '').strip()
        try:
            price_float = float(cleaned_price_str)
            prices.append(price_float)
        except ValueError:
            None

    truncated_titles = [title[:title.find(' ', title.find(' ') + 1)] + '..' if title.find(' ') != -1 else title for title in titles]
    
    plt.figure(figsize=(12, 10))
    plt.bar(truncated_titles, prices, color='skyblue')
    plt.xlabel('Book Title')
    plt.ylabel('Price (£)')
    plt.xticks(rotation=45, ha='right')  # Rotate x-axis labels for better readability
    
    # Save the plot to a BytesIO object
    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    
    # Encode the plot as a base64 string
    plot_data = base64.b64encode(buffer.getvalue()).decode()
    
    plt.close()  # Close the plot to free up memory
    
    return plot_data


@app.route('/info')
def info():
    if 'user' not in session:
        return redirect('/login')

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title, price FROM favorites")
    favorite_books = cursor.fetchall()

    if not favorite_books:
        flash('You have not added any books to favorites yet.', 'info')
        return render_template('info.html', favorite_books=[], plot=None)

    # Plot the prices
    plot_data = plot_prices(favorite_books)

    conn.close()

    return render_template('info.html', favorite_books=favorite_books, plot=plot_data)



if __name__ == '__main__':
    app.run(debug=True)