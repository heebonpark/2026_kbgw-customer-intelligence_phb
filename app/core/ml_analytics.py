import pandas as pd
import numpy as np
import streamlit as st

def render_ml_pipeline(df):
    """
    고급 머신러닝 및 딥러닝 기반의 데이터 분석 UI 및 로직을 렌더링합니다.
    - K-Means 군집 분석
    - Random Forest / MLP 다층퍼셉트론 이탈 위험도 예측
    - Isolation Forest 이상치 탐지
    """
    try:
        from sklearn.cluster import KMeans
        from sklearn.ensemble import RandomForestClassifier, IsolationForest
        from sklearn.neural_network import MLPClassifier
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        st.error("머신러닝 패키지(scikit-learn)가 설치되어 있지 않습니다.")
        return

    st.info("현재 로드된 총괄DB 데이터를 바탕으로 AI 예측 파이프라인(머신러닝/딥러닝)을 실행합니다.")
    
    if df is None or df.empty:
        st.warning("분석할 데이터가 없습니다.")
        return
        
    df_ml = df.copy()
    
    # 1. Feature Engineering
    # 월환산금액 숫자형 변환
    if '월환산금액' in df_ml.columns:
        df_ml['월환산금액'] = pd.to_numeric(df_ml['월환산금액'], errors='coerce').fillna(0)
    else:
        df_ml['월환산금액'] = 0
        
    # 순찰건수 숫자형 변환
    if '순찰건수' in df_ml.columns:
        df_ml['순찰건수'] = pd.to_numeric(df_ml['순찰건수'], errors='coerce').fillna(0)
    else:
        df_ml['순찰건수'] = 0
        
    # VOC 유무 (원 핫 인코딩)
    df_ml['has_voc'] = 0
    if '최근VOC상태' in df_ml.columns:
        df_ml.loc[df_ml['최근VOC상태'].notna(), 'has_voc'] = 1

    # 분석에 사용할 피처 정의
    features = ['월환산금액', '순찰건수', 'has_voc']
    X = df_ml[features]
    
    # 스케일링 (정규화)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    tab1, tab2, tab3 = st.tabs(["🧩 1. 고객 군집 분류 (K-Means)", "🚨 2. 이탈 위험도 예측 (DL/RF)", "🔍 3. 이상치 탐지 (Anomaly)"])
    
    with tab1:
        st.markdown("### 🧩 AI 고객 타겟팅 (K-Means Clustering)")
        st.caption("월정료, 순찰건수, VOC 유무를 종합하여 고객을 3개의 그룹으로 자동 분류합니다.")
        
        # K-Means 머신러닝
        kmeans = KMeans(n_clusters=3, random_state=42, n_init=10)
        df_ml['Cluster'] = kmeans.fit_predict(X_scaled)
        
        # 클러스터 특성 파악 후 라벨링 (임시 로직: 월환산금액 평균 기준)
        cluster_means = df_ml.groupby('Cluster')['월환산금액'].mean()
        sorted_clusters = cluster_means.sort_values().index
        
        cluster_labels = {
            sorted_clusters[0]: "일반 고객군 (Low Value)", 
            sorted_clusters[1]: "관심 필요군 (Mid Value)", 
            sorted_clusters[2]: "VIP 고객군 (High Value)"
        }
        df_ml['고객유형_AI'] = df_ml['Cluster'].map(cluster_labels)
        
        col1, col2 = st.columns([1, 2])
        with col1:
            st.bar_chart(df_ml['고객유형_AI'].value_counts())
        with col2:
            st.dataframe(df_ml[['계약번호', '관리지사', '월환산금액', '순찰건수', 'has_voc', '고객유형_AI']].head(100), use_container_width=True)
        
    with tab2:
        st.markdown("### 🚨 이탈 위험도 예측 (Deep Learning & Random Forest)")
        st.caption("과거 해지된 패턴을 딥러닝(다층 퍼셉트론)과 머신러닝(랜덤포레스트) 앙상블로 학습하여, 현재 고객 중 해지 위험이 높은 고객을 예측합니다.")
        
        # Target variable 생성: 해지 파이프라인에서 넘어온 '일반해지'를 1로 설정
        df_ml['is_churn'] = 0
        if '계약상태(중)_cancelfac' in df_ml.columns:
            df_ml.loc[df_ml['계약상태(중)_cancelfac'] == '일반해지', 'is_churn'] = 1
        elif '계약상태_cancel' in df_ml.columns:
            df_ml.loc[df_ml['계약상태_cancel'].astype(str).str.contains('해지', na=False), 'is_churn'] = 1
        
        churn_count = df_ml['is_churn'].sum()
        if churn_count < 5:
            st.warning(f"학습할 해지 데이터({churn_count}건)가 부족합니다. AI가 충분히 학습하기 위해 최소 5건 이상의 해지 내역이 필요합니다. (6, 7번 데이터 병합 요망)")
        else:
            y = df_ml['is_churn']
            
            # MLP (Deep Learning) + Random Forest 앙상블
            mlp = MLPClassifier(hidden_layer_sizes=(16, 8), max_iter=500, random_state=42)
            rf = RandomForestClassifier(n_estimators=100, random_state=42)
            
            # 모델 훈련
            with st.spinner("AI가 이탈 패턴을 학습하는 중입니다..."):
                mlp.fit(X_scaled, y)
                rf.fit(X_scaled, y)
                
                prob_mlp = mlp.predict_proba(X_scaled)[:, 1]
                prob_rf = rf.predict_proba(X_scaled)[:, 1]
                
                # 앙상블 (두 모델의 예측 확률 평균)
                df_ml['해지위험도(%)'] = ((prob_mlp + prob_rf) / 2 * 100).round(1)
            
            # 현재 해지되지 않은(is_churn==0) 활성 고객 중 위험도가 높은 순 정렬
            active_df = df_ml[df_ml['is_churn'] == 0].sort_values(by='해지위험도(%)', ascending=False)
            
            st.error(f"⚠️ 예측된 이탈 의심 고객 (위험도 순위 TOP 100)")
            st.dataframe(active_df[['계약번호', '관리지사', '월환산금액', '해지위험도(%)']].head(100), use_container_width=True)
            
    with tab3:
        st.markdown("### 🔍 이상치 탐지 (Isolation Forest)")
        st.caption("전체 계약 데이터 중 월정료와 순찰/VOC 패턴이 비정상적으로 동떨어진(Outlier) 특이 데이터를 적발합니다.")
        
        iso = IsolationForest(contamination=0.01, random_state=42)
        df_ml['is_outlier'] = iso.fit_predict(X_scaled)
        
        outliers = df_ml[df_ml['is_outlier'] == -1]
        st.warning(f"적발된 이상치 데이터: {len(outliers)} 건 (전체의 1%)")
        
        st.dataframe(outliers[['계약번호', '관리지사', '월환산금액', '순찰건수', 'has_voc']], use_container_width=True)
