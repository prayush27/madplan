# ml_model.py
# Trains a model to predict course workload
# and returns a penalty score for the scheduling engine

import json
import os
import numpy as np
from sklearn.linear_model import LinearRegression

# Path to our training data
DATA_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'course_difficulty.json')

def load_training_data():
    """Loads the course difficulty dataset."""
    with open(DATA_PATH, 'r') as f:
        return json.load(f)

def train_model():
    """
    Trains a Linear Regression model on our dataset.
    Features: [credits, hours_per_week]
    Target:   workload score
    Returns the trained model.
    """
    data = load_training_data()

    # Build feature matrix X and target vector y
    X = np.array([[d['credits'], d['hours_per_week']] for d in data])
    y = np.array([d['workload'] for d in data])

    model = LinearRegression()
    model.fit(X, y)

    return model

def predict_workload(model, credits, hours_per_week):
    """
    Given a trained model, predicts the workload score
    for a course with the given credits and weekly hours.
    """
    features = np.array([[credits, hours_per_week]])
    return model.predict(features)[0]

def calculate_weekly_hours(course):
    """
    Calculates how many hours per week a course meets.
    Uses the start/end time and number of days.
    """
    start_h = course.start_time // 100
    start_m = course.start_time % 100
    end_h   = course.end_time // 100
    end_m   = course.end_time % 100

    duration_hours = (end_h * 60 + end_m - start_h * 60 - start_m) / 60
    return duration_hours * len(course.days)

def schedule_workload_penalty(model, schedule):
    """
    Predicts the total workload of a schedule.
    Returns a penalty score — higher workload = higher penalty.
    This gets subtracted from the schedule score.
    """
    total_workload = 0
    for course in schedule:
        hours = calculate_weekly_hours(course)
        workload = predict_workload(model, course.credits, hours)
        total_workload += workload

    # Penalty kicks in if total workload exceeds a comfortable threshold
    threshold = 15.0
    if total_workload > threshold:
        penalty = (total_workload - threshold) * 5
        return round(penalty, 2)

    return 0


# Quick test
if __name__ == "__main__":
    from course import sample_courses

    model = train_model()
    print(f"Model trained!")
    print(f"Coefficients: credits={model.coef_[0]:.2f}, hours_per_week={model.coef_[1]:.2f}")
    print(f"Intercept: {model.intercept_:.2f}")
    print()

    # Test prediction
    test_credits = 3
    test_hours   = 3
    predicted    = predict_workload(model, test_credits, test_hours)
    print(f"Predicted workload for {test_credits} credits, {test_hours} hrs/week: {predicted:.2f}")