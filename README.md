# Automated Ambulance Allocation and Dispatch Network

> **A BTech Computer Science Final Year Academic Project developed at Graphic Era University.**

## 📌 Project Overview & Core Idea
When an emergency occurs in a public space, multiple bystanders often dial 108/112 simultaneously. Standard sequential dispatch systems struggle to handle these concurrent requests, leading to critical race conditions—most notably, the double-booking of a single ambulance to multiple callers. 

This project solves that exact problem. By bridging the gap between theoretical AI routing and practical operating system constraints, we have built a concurrency-safe architecture. The system uses a Reinforcement Learning (RL) decision engine backed by robust database locks and custom thread management, ensuring safe, optimized vehicle allocation during high-traffic emergency scenarios without data corruption.

## ⚙️ System Workflow & Architecture
The system follows a strict pipeline to guarantee data integrity and optimal routing:

1. **FastAPI Intake:** The backend securely receives parallel emergency incident requests from simulated external feeds.
2. **Concurrency Queueing:** A custom Python worker thread pool intercepts and manages the simultaneous incoming traffic, preventing server overload and sequential bottlenecks.
3. **Database Concurrency Control:** MySQL's InnoDB engine applies strict row-level locks on ambulance records. If two threads attempt to assign the same ambulance, the lock forces one to wait, entirely preventing race conditions.
4. **Intelligent Routing:** Instead of hardcoded logic, a Reinforcement Learning (RL) model evaluates available resources and calculates the most efficient vehicle assignment.
5. **Cloud & Caching:** Redis caches transient dispatch states and frequent queue operations to maintain high throughput. The final architecture is deployed on AWS to simulate a live, real-world production environment.

## 🛠️ Technology Stack
* **Backend Framework:** Python 3.10+, FastAPI
* **Database Engine:** MySQL (InnoDB for Row-Level Locking)
* **High-Speed Caching:** Redis
* **AI/ML Logic:** Reinforcement Learning (RL) Engine
* **Cloud Infrastructure:** Amazon Web Services (EC2 for the App Server, RDS for the Database)

## 🚀 Local Setup & Installation

### Prerequisites
* Python 3.10 or higher
* MySQL Server (Running locally)
* Redis Server (Running locally)
* Git

### Installation Steps
1. **Clone the repository:**
   ```bash
   git clone [https://github.com/guptapurv07/Automated-Ambulance-Allocation-and-Dispatch-Network.git](https://github.com/guptapurv07/Automated-Ambulance-Allocation-and-Dispatch-Network.git)
   cd Automated-Ambulance-Allocation-and-Dispatch-Network
