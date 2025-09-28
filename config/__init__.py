import pymysql   
              # 1) PyMySQL 모듈을 가져온다
pymysql.install_as_MySQLdb()   # 2) Django가 'MySQLdb'처럼 인식하도록 패치
