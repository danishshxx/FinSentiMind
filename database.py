from sqlalchemy import Column, Integer, String, Float, create_engine, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Engine = create_engine('sqlite:///mydatabase.db')
Base = declarative_base()

class HargaSaham(Base):
    __tablename__ = 'harga_saham'
    id = Column(Integer, primary_key=True)
    tanggal = Column(Date)
    kode_saham = Column(String)
    harga_tutup = Column(Float)
    volume = Column(Integer)

class BeritaSaham(Base):
    __tablename__ = 'berita_saham'
    id = Column(Integer, primary_key=True)
    tanggal = Column(Date)
    kode_saham = Column(String)
    judul_berita = Column(String)
    judul_bersih = Column(String) #Hasil NLP
    